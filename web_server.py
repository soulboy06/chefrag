import os
import sys
import datetime
import uuid
from typing import Dict, List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, Response

# 确保项目根目录在 path 中
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.tools.inventory_tool import InventoryTool
from src.tools.context_tool import ContextTool
from src.tools.user_profile_tool import UserProfileTool
from src.tools.agent_tools import set_session_tools, restore_default_tools
from src.agent.graph import app_graph


# ==============================
# Session 隔离：每人独立状态
# ==============================
SESSION_ID_COOKIE = "chef_session_id"

class SessionState:
    """单个浏览器会话的独立运行状态"""
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.thread_id = str(uuid.uuid4())
        self.is_first_turn = True
        self.city_override: Optional[str] = None

        session_dir = os.path.join("data", "sessions", session_id)
        os.makedirs(session_dir, exist_ok=True)

        self.inventory_tool = InventoryTool(fridge_path=os.path.join(session_dir, "fridge.json"))
        self.profile_tool = UserProfileTool(profile_path=os.path.join(session_dir, "user_profile.json"))
        self.context_tool = ContextTool()


_sessions: Dict[str, SessionState] = {}


def get_or_create_session(request: Request) -> SessionState:
    """从 cookie 获取或创建独立 session"""
    session_id = request.cookies.get(SESSION_ID_COOKIE)
    if not session_id:
        session_id = uuid.uuid4().hex

    if session_id not in _sessions:
        _sessions[session_id] = SessionState(session_id)

    return _sessions[session_id]


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_ID_COOKIE,
        value=session_id,
        httponly=False,
        samesite="Lax",
        max_age=365 * 24 * 60 * 60,
    )

app = FastAPI(title="Chef-RAG 智能餐桌 API 服务")

# 启用 CORS 跨域支持
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
def get_status(request: Request):
    """获取系统基础配置状态、实时定位与天气信息"""
    session = get_or_create_session(request)

    prof = session.profile_tool.get_profile()
    profile_city = prof.get("home_city", "").strip()

    # 确定当前城市
    if session.city_override:
        city = session.city_override
    elif profile_city:
        city = profile_city
    else:
        loc_info = session.context_tool.get_current_location()
        city = loc_info.get("city", "未知城市")

    weather = session.context_tool.get_real_weather(city)
    time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 时令养生分析获取
    try:
        analysis = session.context_tool.get_seasonal_analysis(
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

    response = JSONResponse({
        "time": time_str,
        "city": city,
        "weather": weather,
        "season": season,
        "seasonal_ingredients": seasonal_ingredients,
        "health_tips": health_tips,
        "thread_id": session.thread_id,
    })
    set_session_cookie(response, session.session_id)
    return response

@app.post("/api/location")
def set_location(request: Request, data: Dict[str, str]):
    """手动修改当前临时定位"""
    session = get_or_create_session(request)
    new_city = data.get("city", "").strip()
    if not new_city:
        raise HTTPException(status_code=400, detail="城市名称不能为空")
    session.city_override = new_city
    session.is_first_turn = True  # 重置首轮以应用新城市的气象上下文
    return {"status": "success", "city": session.city_override}

@app.get("/api/fridge")
def get_fridge(request: Request):
    """获取冰箱食材列表"""
    session = get_or_create_session(request)
    try:
        inv = session.inventory_tool.get_inventory()
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
def add_fridge_item(request: Request, item: FridgeItemRequest):
    """添加或修改冰箱食材"""
    session = get_or_create_session(request)
    try:
        # 拼接详情字符串，如 "500g | 剩 2 天" 或 "3 个"
        detail = item.quantity
        if item.expiry_days is not None:
            detail += f" | 剩 {item.expiry_days} 天"

        session.inventory_tool.update_inventory({item.name: detail})
        return {"status": "success", "inventory": session.inventory_tool.get_inventory()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/fridge/{name}")
def delete_fridge_item(request: Request, name: str):
    """物理删除冰箱中指定食材"""
    session = get_or_create_session(request)
    try:
        session.inventory_tool.remove_item(name)
        return {"status": "success", "inventory": session.inventory_tool.get_inventory()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/profile")
def get_profile(request: Request):
    """获取用户画像设置"""
    session = get_or_create_session(request)
    try:
        return session.profile_tool.get_profile()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/profile")
def update_profile(request: Request, data: ProfileRequest):
    """更新用户画像设置"""
    session = get_or_create_session(request)
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

        session.profile_tool.update_profile(update_data)
        return {"status": "success", "profile": session.profile_tool.get_profile()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
def chat(request: Request, chat_req: ChatRequest):
    """多轮对话接口，流转大厨 Agent 状态图"""
    session = get_or_create_session(request)

    user_msg = chat_req.message.strip()
    if not user_msg:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    config = {"configurable": {"thread_id": session.thread_id}}

    try:
        # 如果是该 thread_id 的第一轮对话，补充环境上下文
        if session.is_first_turn:
            # 获取定位与天气
            prof = session.profile_tool.get_profile()
            profile_city = prof.get("home_city", "").strip()

            if session.city_override:
                city = session.city_override
            elif profile_city:
                city = profile_city
            else:
                city = session.context_tool.get_current_location().get("city", "深圳市")

            weather = session.context_tool.get_real_weather(city)

            state_input = {
                "user_query": user_msg,
                "location": city,
                "time": datetime.datetime.now(),
                "weather": weather,
                "chat_history": []
            }
            session.is_first_turn = False
        else:
            state_input = {
                "user_query": user_msg
            }

        # 注入当前 session 的工具实例，确保 Agent 与前端共用同一数据源
        set_session_tools(session.inventory_tool, session.profile_tool)
        try:
            # 调用 LangGraph 流流转
            result = app_graph.invoke(state_input, config)
        finally:
            restore_default_tools()

        response_data = {
            "message": result.get("message", "未获得有效决策。"),
            "selected_recipe": result.get("selected_recipe"),
            "recommendations": result.get("recommendations", [])
        }

        response = JSONResponse(response_data)
        set_session_cookie(response, session.session_id)
        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent 流转异常: {str(e)}")

@app.post("/api/reset")
def reset_session(request: Request):
    """重置会话，重新生成 thread_id"""
    session = get_or_create_session(request)

    # 模拟退出，触发一次记忆归档
    try:
        set_session_tools(session.inventory_tool, session.profile_tool)
        try:
            app_graph.invoke({
                "user_query": "整理全量会话画像并保存配置",
                "save_memory": True
            }, {"configurable": {"thread_id": session.thread_id}})
        finally:
            restore_default_tools()
    except Exception:
        pass

    session.thread_id = str(uuid.uuid4())
    session.is_first_turn = True
    response = JSONResponse({"status": "success", "thread_id": session.thread_id})
    set_session_cookie(response, session.session_id)
    return response

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
