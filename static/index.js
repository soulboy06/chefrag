// 智能 API 根路径自动容灾重定向（防止直接双击打开本地 HTML 导致 file:/// 相对路径连接失败）
const API_BASE = window.location.protocol === 'file:' ? 'http://127.0.0.1:8000' : '';

// DOM 元素引用
const elLocation = document.getElementById('current-location');
const elCityInput = document.getElementById('city-input');
const elBtnUpdateCity = document.getElementById('btn-update-city');
const elWeatherIcon = document.getElementById('weather-icon');
const elWeatherMsg = document.getElementById('weather-message');
const elAvoidTagsList = document.getElementById('avoid-tags-list');
const elNewAvoidInput = document.getElementById('new-avoid-input');
const elBtnAddAvoid = document.getElementById('btn-add-avoid');
const elWeatherAdapt = document.getElementById('weather-adapt-switch');
const elBtnReset = document.getElementById('btn-reset-session');

const elChatScroller = document.getElementById('chat-scroller');
const elChatInput = document.getElementById('chat-input');
const elBtnMic = document.getElementById('btn-mic');
const elBtnSend = document.getElementById('btn-send');

const elFridgeCount = document.getElementById('fridge-count');
const elFridgeListName = document.getElementById('fridge-item-name');
const elFridgeListQty = document.getElementById('fridge-item-qty');
const elFridgeListExpiry = document.getElementById('fridge-item-expiry');
const elBtnAddFridge = document.getElementById('btn-add-fridge');
const elFridgeItemList = document.getElementById('fridge-item-list');

// 全局配置
let avoidIngredientsList = [];

// 初始化加载
document.addEventListener('DOMContentLoaded', () => {
    // 首屏加载时，立即优先初始化解析页面上的所有 Lucide 图标，避免异步网络延迟导致卡顿变空白
    if (window.lucide) {
        lucide.createIcons();
    }
    initApp();
    setupEventListeners();
});

// 初始化数据
async function initApp() {
    await fetchStatus();
    await fetchProfile();
    await fetchFridge();
}

// 获取定位与天气状态
async function fetchStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/status`);
        if (!res.ok) throw new Error('获取系统状态失败');
        const data = await res.json();
        
        elLocation.textContent = data.city;
        elCityInput.value = data.city;
        
        // 气象与养生贴士
        const wea = data.weather !== '未知' ? `${data.weather} | ` : '';
        const seasonStr = data.season ? `【${data.season}养生建议】` : '';
        elWeatherMsg.textContent = `定位: ${data.city} (${wea}实时天气) ${seasonStr} ${data.health_tips}`;
        
        updateWeatherIcon(data.weather);
    } catch (err) {
        console.error(err);
        elWeatherMsg.textContent = '小厨娘暂时无法探测到当前气象，建议多喝热水，健康饮食哦～☕';
    }
}

// 更新天气图标
function updateWeatherIcon(weather) {
    if (!weather) return;
    const w = weather.toLowerCase();
    let iconName = 'sun';
    if (w.includes('雨')) iconName = 'cloud-rain';
    else if (w.includes('阴') || w.includes('云')) iconName = 'cloud';
    else if (w.includes('雪')) iconName = 'snowflake';
    else if (w.includes('风')) iconName = 'wind';
    
    elWeatherIcon.setAttribute('data-lucide', iconName);
    if (window.lucide) {
        lucide.createIcons();
    }
}

// 获取用户配置
async function fetchProfile() {
    try {
        const res = await fetch(`${API_BASE}/api/profile`);
        if (!res.ok) throw new Error('获取用户偏好失败');
        const data = await res.json();
        
        // 高亮饮食偏好
        const goal = data.diet_goal || '健康膳食 (营养均衡)';
        const cards = document.querySelectorAll('.pref-card');
        cards.forEach(card => {
            const cardGoal = card.getAttribute('data-goal');
            // 进行弹性匹配，避免微小中英文/空格的差异
            if (cardGoal === goal || (goal && cardGoal && (goal.includes(cardGoal.substring(0, 4)) || cardGoal.includes(goal.substring(0, 4))))) {
                card.classList.add('active');
                card.querySelector('.check-icon').classList.remove('hide');
            } else {
                card.classList.remove('active');
                card.querySelector('.check-icon').classList.add('hide');
            }
        });
        
        // 天气开关
        elWeatherAdapt.checked = data.workday_lunch_delivery || false;
        
        // 忌口标签
        avoidIngredientsList = data.avoid_ingredients || [];
        renderAvoidTags();
    } catch (err) {
        console.error(err);
    }
}

// 渲染忌口标签
function renderAvoidTags() {
    elAvoidTagsList.innerHTML = '';
    if (avoidIngredientsList.length === 0) {
        elAvoidTagsList.innerHTML = '<span class="pref-desc" style="color: var(--fg-muted)">暂无忌口食材</span>';
        return;
    }
    
    avoidIngredientsList.forEach(item => {
        const tag = document.createElement('div');
        tag.className = 'avoid-tag';
        tag.innerHTML = `
            <span>${item}</span>
            <div class="remove-tag-btn" onclick="removeAvoidIngredient('${item}')">✕</div>
        `;
        elAvoidTagsList.appendChild(tag);
    });
}

// 添加忌口食材
async function addAvoidIngredient() {
    const value = elNewAvoidInput.value.trim();
    if (!value) return;
    
    if (avoidIngredientsList.includes(value)) {
        elNewAvoidInput.value = '';
        return;
    }
    
    avoidIngredientsList.push(value);
    elNewAvoidInput.value = '';
    renderAvoidTags();
    await saveProfile();
}

// 移除忌口食材
async function removeAvoidIngredient(name) {
    avoidIngredientsList = avoidIngredientsList.filter(item => item !== name);
    renderAvoidTags();
    await saveProfile();
}

// 提交画像保存
async function saveProfile() {
    try {
        const activeCard = document.querySelector('.pref-card.active');
        const goal = activeCard ? activeCard.getAttribute('data-goal') : '健康膳食 (营养均衡)';
        
        await fetch(`${API_BASE}/api/profile`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                diet_goal: goal,
                avoid_ingredients: avoidIngredientsList,
                workday_lunch_delivery: elWeatherAdapt.checked
            })
        });
    } catch (err) {
        console.error(err);
    }
}

// 智能判断冰箱食材图标
function getFridgeItemIcon(name) {
    const n = name.toLowerCase();
    if (n.match(/(肉|排骨|鸡|鸭|猪|牛|羊|鹅|排|翅)/)) return 'beef';
    if (n.match(/(鱼|虾|蟹|贝|海鲜|蚝|鱿|蚌|螺)/)) return 'fish';
    if (n.match(/(蛋|卵)/)) return 'egg';
    if (n.match(/(菜|西兰花|茄|椒|菇|木耳|耳|萝|葱|蒜|姜|薯|土豆|洋葱|番茄|西红柿|瓜|豆|笋|芹|香菜)/)) return 'leaf';
    if (n.match(/(苹果|香蕉|梨|桃|橘|橙|草莓|葡萄|瓜|果|柠檬|蓝莓|芒果)/)) return 'apple';
    if (n.match(/(油|盐|酱|醋|糖|奶|汁|水|饮料|酒|蜜|粉|膏|精|味精|面|米|粮)/)) return 'droplet';
    return 'package';
}

// 获取冰箱库存
async function fetchFridge() {
    try {
        const res = await fetch(`${API_BASE}/api/fridge`);
        if (!res.ok) throw new Error('获取冰箱列表失败');
        const items = await res.json();
        
        elFridgeCount.textContent = `${items.length}/10`;
        renderFridgeItems(items);
    } catch (err) {
        console.error(err);
    }
}

// 渲染冰箱食材
function renderFridgeItems(items) {
    elFridgeItemList.innerHTML = '';
    if (items.length === 0) {
        elFridgeItemList.innerHTML = `
            <div class="empty-state">
                <svg class="empty-fridge-svg" viewBox="0 0 100 120" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <rect x="20" y="10" width="60" height="100" rx="8" stroke="#8E7A4A" stroke-width="2.5" fill="rgba(142, 122, 74, 0.05)"/>
                    <line x1="20" y1="55" x2="80" y2="55" stroke="#8E7A4A" stroke-width="2"/>
                    <rect x="25" y="30" width="4" height="15" rx="2" fill="#8E7A4A"/>
                    <rect x="25" y="65" width="4" height="20" rx="2" fill="#8E7A4A"/>
                    <circle cx="50" cy="85" r="8" stroke="#8E7A4A" stroke-width="1.5" stroke-dasharray="2 2"/>
                </svg>
                <p>冰箱空空如也，快入库食材吧～</p>
            </div>
        `;
        return;
    }
    
    items.forEach(item => {
        const card = document.createElement('div');
        card.className = 'fridge-item-card';
        card.setAttribute('title', '点击将食材名字导入输入框');
        
        let expiryClass = 'expired-normal';
        let detailClass = '';
        let days = null;
        
        // 匹配保质天数，若 <= 2 天，进行红色高亮闪烁
        const dayMatch = item.expiry.match(/剩\s*(\d+)\s*天/);
        if (dayMatch) {
            days = parseInt(dayMatch[1]);
            if (days <= 2) {
                expiryClass = 'expired-warn';
                detailClass = 'danger';
            }
        }
        
        const iconName = getFridgeItemIcon(item.name);
        
        // 增加独特的卡片内布局
        card.innerHTML = `
            <div class="fridge-item-left" onclick="appendIngredientToInput('${item.name}')">
                <div class="expiry-dot ${expiryClass}"></div>
                <div style="background-color: var(--accent-light); padding: 6px; border-radius: var(--round-xs); display: flex; align-items: center; justify-content: center; color: var(--accent-primary);">
                    <i data-lucide="${iconName}" style="width: 16px; height: 16px;"></i>
                </div>
                <div class="fridge-item-text">
                    <span class="fridge-item-name">${item.name}</span>
                    <span class="fridge-item-details ${detailClass}">${item.quantity} | ${item.expiry}</span>
                </div>
            </div>
            <div class="fridge-item-actions">
                <i data-lucide="trash-2" class="action-icon trash" title="删除该食材" onclick="deleteFridgeItem(event, '${item.name}', this)"></i>
            </div>
        `;
        elFridgeItemList.appendChild(card);
    });
    
    if (window.lucide) {
        lucide.createIcons();
    }
}

// 点击冰箱食材直接追加到输入框
function appendIngredientToInput(name) {
    const val = elChatInput.value.trim();
    if (val.includes(name)) return;
    elChatInput.value = val ? `${val}、${name}` : name;
    elChatInput.focus();
}

// 添加冰箱食材
async function addFridgeItem() {
    const name = elFridgeListName.value.trim();
    const qty = elFridgeListQty.value.trim() || '若干';
    const exp = elFridgeListExpiry.value.trim();
    
    if (!name) return;
    
    try {
        const body = { name, quantity: qty };
        if (exp) body.expiry_days = parseInt(exp);
        
        await fetch(`${API_BASE}/api/fridge`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        
        elFridgeListName.value = '';
        elFridgeListQty.value = '';
        elFridgeListExpiry.value = '';
        
        await fetchFridge();
    } catch (err) {
        console.error(err);
    }
}

// 删除冰箱食材 (带平滑向右滑出滑落动画)
async function deleteFridgeItem(event, name, iconEl) {
    event.stopPropagation();
    const card = iconEl.closest('.fridge-item-card');
    if (card) {
        card.classList.add('removing');
        // 等待 CSS 动画播放完毕后再执行请求与重绘
        await new Promise(resolve => setTimeout(resolve, 380));
    }
    try {
        await fetch(`${API_BASE}/api/fridge/${name}`, { method: 'DELETE' });
        await fetchFridge();
    } catch (err) {
        console.error(err);
    }
}

// 手动保存城市定位
async function updateCity() {
    const city = elCityInput.value.trim();
    if (!city) return;
    
    try {
        await fetch(`${API_BASE}/api/location`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ city })
        });
        
        await fetch(`${API_BASE}/api/profile`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ home_city: city })
        });
        
        await fetchStatus();
    } catch (err) {
        console.error(err);
    }
}

// 全局打字机流式输出状态
let currentTypingTimer = null;
let currentTypingElement = null;
let currentTypingFullText = '';

function finishCurrentTyping() {
    if (currentTypingTimer) {
        clearInterval(currentTypingTimer);
        currentTypingTimer = null;
    }
    if (currentTypingElement && currentTypingFullText) {
        currentTypingElement.innerHTML = currentTypingFullText.replace(/\n/g, '<br>');
        currentTypingElement = null;
        currentTypingFullText = '';
        scrollChatToBottom();
    }
}

function startTyping(element, text) {
    currentTypingElement = element;
    currentTypingFullText = text;
    
    let index = 0;
    element.innerHTML = '<span class="typing-cursor">▮</span>';
    
    currentTypingTimer = setInterval(() => {
        if (index < text.length) {
            index++;
            const rawChar = text.substring(0, index);
            element.innerHTML = rawChar.replace(/\n/g, '<br>') + '<span class="typing-cursor">▮</span>';
            scrollChatToBottom();
        } else {
            finishCurrentTyping();
        }
    }, 12); // 每 12 毫秒打出一个字符，速度丝滑如流式
}

// 发送用户提问
async function handleSendMessage() {
    const query = elChatInput.value.trim();
    if (!query) return;
    
    finishCurrentTyping(); // 发送新消息前，强制完成上一次正在打字的动画
    appendMessage(query, 'user');
    elChatInput.value = '';
    
    const loaderId = appendLoader();
    
    try {
        const res = await fetch(`${API_BASE}/api/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: query })
        });
        
        if (!res.ok) throw new Error('大厨连接超时');
        const data = await res.json();
        
        removeLoader(loaderId);
        appendMessage(data.message, 'ai');
        
        // 若进行了食材消耗，重新拉取冰箱列表以实现完美同步
        if (data.selected_recipe) {
            await fetchFridge();
            appendRecipeCard(data.selected_recipe);
        }
        
    } catch (err) {
        console.error(err);
        removeLoader(loaderId);
        appendMessage('抱歉主子，小厨娘刚刚好像开小差了，要不我们再试一次吧？🍳💖', 'ai');
    }
}

// 往对话流中追加气泡，并带平滑滚动动效
function appendMessage(text, sender) {
    finishCurrentTyping(); // 强制收尾之前的打字状态
    
    const bubble = document.createElement('div');
    bubble.className = `message-bubble message-${sender}`;
    
    const avatarHtml = sender === 'user' 
        ? `<div class="avatar-bubble">
             <svg class="avatar-mini-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 20px; height: 20px; color: #FFF; margin: 8px;">
                 <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>
             </svg>
           </div>`
        : `<div class="avatar-bubble">
             <svg class="avatar-mini-svg" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
                 <circle cx="32" cy="32" r="30" fill="#8E7A4A"/>
                 <path d="M22 28C22 23.5 25.5 20 30 20H34C38.5 20 42 23.5 42 28C42 30 40 31.5 38.5 32.5V36H25.5V32.5C24 31.5 22 30 22 28Z" stroke="#FFF" stroke-width="2.5" fill="rgba(255,255,255,0.2)"/>
                 <rect x="24" y="36" width="16" height="4" rx="2" fill="#FFF"/>
             </svg>
           </div>`;

    bubble.innerHTML = `
        ${avatarHtml}
        <div class="bubble-content"></div>
    `;
    
    elChatScroller.appendChild(bubble);
    scrollChatToBottom();
    
    const contentDiv = bubble.querySelector('.bubble-content');
    if (sender === 'user') {
        contentDiv.innerHTML = text.replace(/\n/g, '<br>');
    } else {
        startTyping(contentDiv, text);
    }
}

// 插入 Loading 骨架屏
function appendLoader() {
    const loaderId = 'loader-' + Date.now();
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble message-ai';
    bubble.id = loaderId;
    
    const avatarHtml = `<div class="avatar-bubble">
         <svg class="avatar-mini-svg" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
             <circle cx="32" cy="32" r="30" fill="#8E7A4A"/>
             <path d="M22 28C22 23.5 25.5 20 30 20H34C38.5 20 42 23.5 42 28C42 30 40 31.5 38.5 32.5V36H25.5V32.5C24 31.5 22 30 22 28Z" stroke="#FFF" stroke-width="2.5" fill="rgba(255,255,255,0.2)"/>
             <rect x="24" y="36" width="16" height="4" rx="2" fill="#FFF"/>
         </svg>
       </div>`;

    bubble.innerHTML = `
        ${avatarHtml}
        <div class="bubble-content">
            <div class="typing-loader">
                <span></span>
                <span></span>
                <span></span>
            </div>
        </div>
    `;
    elChatScroller.appendChild(bubble);
    scrollChatToBottom();
    return loaderId;
}

// 移除 Loading
function removeLoader(loaderId) {
    const loader = document.getElementById(loaderId);
    if (loader) loader.remove();
}

// 渲染精美的高拟真食谱大卡片
function appendRecipeCard(recipe) {
    const wrapper = document.createElement('div');
    wrapper.className = 'recipe-card-wrapper';
    
    const tagsHtml = recipe.tags ? recipe.tags.map(t => `<span class="recipe-tag">${t}</span>`).join('') : '';
    
    // 带有法式拟物手绘餐单的结构与内联 SVG 装饰
    wrapper.innerHTML = `
        <div class="recipe-card">
            <!-- 精美小手绘餐盘与勺叉 SVG 艺术装饰点缀在右上角 -->
            <svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg" style="position: absolute; right: 12px; top: 12px; width: 44px; height: 44px; opacity: 0.15;">
                <circle cx="50" cy="50" r="40" stroke="#8E7A4A" stroke-width="3"/>
                <path d="M50 25V75" stroke="#8E7A4A" stroke-width="2" stroke-dasharray="3 3"/>
                <path d="M30 40L40 50L30 60" stroke="#8E7A4A" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
                <path d="M70 40L60 50L70 60" stroke="#8E7A4A" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <div class="recipe-card-header">
                <span class="recipe-title">${recipe.name}</span>
                <span class="match-badge">95% 推荐匹配度</span>
            </div>
            <div class="recipe-tags-row">
                ${tagsHtml}
                <span class="recipe-tag avoided">已避开忌口食材</span>
            </div>
            <div class="recipe-summary-text">
                <strong>所需用料：</strong>${recipe.ingredients.join('、')}<br><br>
                <strong>做法简介：</strong>此食谱已完美过滤您的忌口。点击下方按钮即可查看具体步骤！
            </div>
            <button class="btn-cook" onclick="askRecipeSteps('${recipe.name}')">开始烹饪，查看步骤</button>
        </div>
    `;
    
    elChatScroller.appendChild(wrapper);
    scrollChatToBottom();
}

// 用户点击“开始烹饪”，直接模拟发送问题，让大模型返回做法步骤
function askRecipeSteps(recipeName) {
    elChatInput.value = `确认做【${recipeName}】，请告诉我详细的步骤，帮我扣减食材库存。`;
    handleSendMessage();
}

// 平滑滚动到底部
function scrollChatToBottom() {
    elChatScroller.scrollTo({
        top: elChatScroller.scrollHeight,
        behavior: 'smooth'
    });
}

// 重置会话，提炼记忆
async function handleResetSession() {
    if (!confirm('确定要重置当前对话吗？重置时会将本轮会话沉淀并压缩为您的偏好记忆。')) return;
    
    try {
        const res = await fetch(`${API_BASE}/api/reset`, { method: 'POST' });
        if (!res.ok) throw new Error('重置失败');
        
        elChatScroller.innerHTML = `
            <div class="message-bubble message-ai">
                <div class="avatar-bubble">
                    <svg class="avatar-mini-svg" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <circle cx="32" cy="32" r="30" fill="#8E7A4A"/>
                        <path d="M22 28C22 23.5 25.5 20 30 20H34C38.5 20 42 23.5 42 28C42 30 40 31.5 38.5 32.5V36H25.5V32.5C24 31.5 22 30 22 28Z" stroke="#FFF" stroke-width="2.5" fill="rgba(255,255,255,0.2)"/>
                        <rect x="24" y="36" width="16" height="4" rx="2" fill="#FFF"/>
                    </svg>
                </div>
                <div class="bubble-content">
                    会话重置成功！偏好记忆已自动归档！🎨✨<br>
                    冰箱中的食材库存已妥善保留。请问接下来想吃点什么，或者有任何做菜的困惑吗？
                </div>
            </div>
        `;
        
        await initApp();
    } catch (err) {
        console.error(err);
    }
}

// 事件绑定
function setupEventListeners() {
    // 城市定位保存
    elBtnUpdateCity.addEventListener('click', updateCity);
    elCityInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') updateCity();
    });
    
    // 偏好卡片单选切换
    const cards = document.querySelectorAll('.pref-card');
    cards.forEach(card => {
        card.addEventListener('click', async () => {
            cards.forEach(c => {
                c.classList.remove('active');
                c.querySelector('.check-icon').classList.add('hide');
            });
            card.classList.add('active');
            card.querySelector('.check-icon').classList.remove('hide');
            await saveProfile();
        });
    });
    
    // 忌口添加
    elBtnAddAvoid.addEventListener('click', addAvoidIngredient);
    elNewAvoidInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addAvoidIngredient();
    });
    
    // 天气自适应 Toggle 改变
    elWeatherAdapt.addEventListener('change', async () => {
        await saveProfile();
        await fetchStatus();
    });
    
    // 重置会话
    elBtnReset.addEventListener('click', handleResetSession);
    
    // 冰箱食材添加
    elBtnAddFridge.addEventListener('click', addFridgeItem);
    [elFridgeListName, elFridgeListQty, elFridgeListExpiry].forEach(el => {
        el.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') addFridgeItem();
        });
    });
    
    // 消息发送
    elBtnSend.addEventListener('click', handleSendMessage);
    elChatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') handleSendMessage();
    });
    
    // 灵感星星按钮快捷输入
    elBtnMic.addEventListener('click', () => {
        // 心跳放大微动效
        elBtnMic.style.transform = 'scale(1.2)';
        setTimeout(() => { elBtnMic.style.transform = ''; }, 200);
        
        elChatInput.value = '今天冰箱里有什么？给我推荐一下！';
        elChatInput.focus();
    });
}
