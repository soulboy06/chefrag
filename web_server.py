import os
import sys
import datetime
import uuid
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

# 确保项目根目录在 path 中
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.tools.inventory_tool import InventoryTool
from src.tools.context_tool import ContextTool
from src.tools.user_profile_tool import UserProfileTool
from src.agent.graph import app_graph

app = FastAPI(title="Chef-RAG 智能餐桌 API 服务")

# 启用 CORS 跨域支持
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化底层业务工具
inventory_tool = InventoryTool()
context_tool = ContextTool()
profile_tool = UserProfileTool()

# 全局会话与状态管理器
current_thread_id = str(uuid.uuid4())
is_first_turn = True
current_city_override = None  # 允许手动更改定位

# Pydantic 交互请求模型定义
class FridgeItemRequest(BaseModel):
    name: str
    quantity: str
    expiry_days: Optional[int] = None

class ProfileRequest(BaseModel):
    home_city: Optional[str] = None
    diet_goal: Optional[str] = None
    avoid_ingredients: Optional[List[str]] = None
    workday_lunch_delivery: Optional[bool] = None

class ChatRequest(BaseModel):
    message: str

# API 接口路由定义

@app.get("/api/status")
def get_status():
    """获取系统基础配置状态、实时定位与天气信息"""
    global current_city_override
    
    prof = profile_tool.get_profile()
    profile_city = prof.get("home_city", "").strip()
    
    # 确定当前城市
    if current_city_override:
        city = current_city_override
    elif profile_city:
        city = profile_city
    else:
        loc_info = context_tool.get_current_location()
        city = loc_info.get("city", "未知城市")
        
    weather = context_tool.get_real_weather(city)
    time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 时令养生分析获取
    try:
        analysis = context_tool.get_seasonal_analysis(
            city=city, 
            date_obj=datetime.date.today(), 
            weather=weather if weather != "未知" else "多云"
        )
        season = analysis.get("season", "四季")
        seasonal_ingredients = analysis.get("suggested_ingredients", [])
        health_tips = analysis.get("health_tips", "夏令时令，合理养生。")
    except Exception:
        season = "四季"
        seasonal_ingredients = []
        health_tips = "多喝温水，合理膳食。"
        
    return {
        "time": time_str,
        "city": city,
        "weather": weather,
        "season": season,
        "seasonal_ingredients": seasonal_ingredients,
        "health_tips": health_tips,
        "thread_id": current_thread_id
    }

@app.post("/api/location")
def set_location(data: Dict[str, str]):
    """手动修改当前临时定位"""
    global current_city_override, is_first_turn
    new_city = data.get("city", "").strip()
    if not new_city:
        raise HTTPException(status_code=400, detail="城市名称不能为空")
    current_city_override = new_city
    is_first_turn = True  # 重置首轮以应用新城市的气象上下文
    return {"status": "success", "city": current_city_override}

@app.get("/api/fridge")
def get_fridge():
    """获取冰箱食材列表"""
    try:
        inv = inventory_tool.get_inventory()
        # 将结构转化为列表形式，便于前端渲染
        items = []
        for name, detail in inv.items():
            # 格式化，牛肉: '500g | 剩 2 天' -> 拆分方便前端读取
            qty = "若干"
            exp = "未知"
            if detail:
                parts = detail.split("|")
                qty = parts[0].strip()
                if len(parts) > 1:
                    exp = parts[1].strip()
            items.append({
                "name": name,
                "quantity": qty,
                "expiry": exp,
                "raw_detail": detail
            })
        return items
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/fridge")
def add_fridge_item(item: FridgeItemRequest):
    """添加或修改冰箱食材"""
    try:
        # 拼接详情字符串，如 "500g | 剩 2 天" 或 "3 个"
        detail = item.quantity
        if item.expiry_days is not None:
            detail += f" | 剩 {item.expiry_days} 天"
            
        inventory_tool.update_inventory({item.name: detail})
        return {"status": "success", "inventory": inventory_tool.get_inventory()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/fridge/{name}")
def delete_fridge_item(name: str):
    """物理删除冰箱中指定食材"""
    try:
        inventory_tool.remove_item(name)
        return {"status": "success", "inventory": inventory_tool.get_inventory()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/profile")
def get_profile():
    """获取用户画像设置"""
    try:
        return profile_tool.get_profile()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/profile")
def update_profile(data: ProfileRequest):
    """更新用户画像设置"""
    try:
        update_data = {}
        if data.home_city is not None:
            update_data["home_city"] = data.home_city
        if data.diet_goal is not None:
            update_data["diet_goal"] = data.diet_goal
        if data.avoid_ingredients is not None:
            update_data["avoid_ingredients"] = data.avoid_ingredients
        if data.workday_lunch_delivery is not None:
            update_data["workday_lunch_delivery"] = data.workday_lunch_delivery
            
        profile_tool.update_profile(update_data)
        return {"status": "success", "profile": profile_tool.get_profile()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
def chat(request: ChatRequest):
    """多轮对话接口，流转大厨 Agent 状态图"""
    global is_first_turn, current_thread_id, current_city_override
    
    user_msg = request.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="消息内容不能为空")
        
    config = {"configurable": {"thread_id": current_thread_id}}
    
    try:
        # 如果是该 thread_id 的第一轮对话，补充环境上下文
        if is_first_turn:
            # 获取定位与天气
            prof = profile_tool.get_profile()
            profile_city = prof.get("home_city", "").strip()
            
            if current_city_override:
                city = current_city_override
            elif profile_city:
                city = profile_city
            else:
                city = context_tool.get_current_location().get("city", "深圳市")
                
            weather = context_tool.get_real_weather(city)
            
            state_input = {
                "user_query": user_msg,
                "location": city,
                "time": datetime.datetime.now(),
                "weather": weather,
                "chat_history": []
            }
            is_first_turn = False
        else:
            state_input = {
                "user_query": user_msg
            }
            
        # 调用 LangGraph 流流转
        result = app_graph.invoke(state_input, config)
        
        response_data = {
            "message": result.get("message", "未获得有效决策。"),
            "selected_recipe": result.get("selected_recipe"),
            "recommendations": result.get("recommendations", [])
        }
        
        return response_data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent 流转异常: {str(e)}")

@app.post("/api/reset")
def reset_session():
    """重置会话，重新生成 thread_id"""
    global current_thread_id, is_first_turn
    
    # 模拟退出，触发一次记忆归档
    try:
        app_graph.invoke({
            "user_query": "整理全量会话画像并保存配置",
            "save_memory": True
        }, {"configurable": {"thread_id": current_thread_id}})
    except Exception:
        pass
        
    current_thread_id = str(uuid.uuid4())
    is_first_turn = True
    return {"status": "success", "thread_id": current_thread_id}

# 挂载静态资源托管前端静态页面（static 文件夹必须存在）
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    # 检测大模型 API Key 状态，给出后台友好提示
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[Warning] 未检测到 OPENAI_API_KEY！系统将自动在纯本地降级规则库模式下运行。")
    print("[System] Chef-RAG API 服务已成功启动！请在浏览器访问 http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
