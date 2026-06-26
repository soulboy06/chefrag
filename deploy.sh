#!/bin/bash
set -e

# ============================================================
# Chef-RAG 一键部署脚本
# 域名: soulboy.xyz
# 用法: sudo bash deploy.sh
# ============================================================

DOMAIN="soulboy.xyz"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PORT=8000

echo "========================================="
echo "  Chef-RAG 部署脚本"
echo "  域名: $DOMAIN"
echo "  项目路径: $PROJECT_DIR"
echo "========================================="

# 1. 安装系统依赖
echo ""
echo "[1/6] 安装系统依赖..."
apt update -y
apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx

# 2. 创建 Python 虚拟环境并安装依赖
echo ""
echo "[2/6] 配置 Python 虚拟环境..."
cd "$PROJECT_DIR"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
echo "Python 依赖安装完成。"

# 3. 创建 systemd 服务
echo ""
echo "[3/6] 创建 systemd 服务..."
cat > /etc/systemd/system/chefrag.service << EOF
[Unit]
Description=Chef-RAG 智能餐桌 Web 服务
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/venv/bin"
ExecStart=$PROJECT_DIR/venv/bin/python -m uvicorn web_server:app --host 0.0.0.0 --port $APP_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable chefrag
systemctl restart chefrag
echo "systemd 服务已创建并启动。"

# 等待服务启动
sleep 2
if systemctl is-active --quiet chefrag; then
    echo "Chef-RAG 服务运行正常。"
else
    echo "警告: Chef-RAG 服务启动失败，请检查日志: journalctl -u chefrag -f"
fi

# 4. 配置 Nginx 反向代理（先 HTTP，certbot 会自动改为 HTTPS）
echo ""
echo "[4/6] 配置 Nginx..."
cat > /etc/nginx/sites-available/chefrag << EOF
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }

    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf)$ {
        proxy_pass http://127.0.0.1:$APP_PORT;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
EOF

# 启用站点配置
ln -sf /etc/nginx/sites-available/chefrag /etc/nginx/sites-enabled/chefrag

# 删除默认站点（避免冲突）
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl reload nginx
echo "Nginx 配置完成。"

# 5. 配置防火墙
echo ""
echo "[5/6] 配置防火墙..."
if command -v ufw &> /dev/null; then
    ufw allow 'Nginx Full'
    ufw allow ssh
    ufw --force enable
    echo "防火墙已配置。"
else
    echo "未检测到 ufw，跳过防火墙配置。请手动确保 80/443 端口已开放。"
fi

# 6. 申请 SSL 证书 (Let's Encrypt)
echo ""
echo "[6/6] 申请 SSL 证书 (Let's Encrypt)..."
echo "注意: certbot 会要求你输入邮箱并同意服务条款。"
echo ""
certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --non-interactive --agree-tos --email admin@$DOMAIN --redirect

# 设置自动续期
echo "0 3 * * * root certbot renew --quiet --post-hook 'systemctl reload nginx'" > /etc/cron.d/certbot-renew
echo "SSL 证书已申请，自动续期已配置。"

echo ""
echo "========================================="
echo "  部署完成！"
echo ""
echo "  访问地址: https://$DOMAIN"
echo "  HTTP 会自动跳转到 HTTPS"
echo ""
echo "  常用命令:"
echo "    查看服务状态: systemctl status chefrag"
echo "    查看实时日志: journalctl -u chefrag -f"
echo "    重启服务:     systemctl restart chefrag"
echo "    查看 Nginx:   systemctl status nginx"
echo "========================================="
