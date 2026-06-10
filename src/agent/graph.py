import os
import re
import datetime
from typing import Dict, Any, List, TypedDict, Optional, Literal
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from dotenv import load_dotenv

# 从模块化重构后的工具类中导入全部的 tools 和核心依赖实例
from src.tools.agent_tools import (
    tools,
    tools_map,
    inventory_tool,
    context_tool,
    profile_tool,
    retriever,
    state_container,
    deduct_fridge_ingredients
)

load_dotenv()

def check_api_connectivity(url: str, timeout: float = 1.0) -> bool:
    """
    轻量级探测大模型接口域名连通性（利用底层 socket 和极简 HTTP 握手双重保障，防范网络阻断与响应死锁）
    """
    import urllib.parse
    import socket
    import requests
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return False
        # 1. 探测 socket 是否通
        socket.setdefaulttimeout(timeout)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        s.close()
        
        # 2. 探测 HTTP 请求是否通（针对可连接但访问极慢的网络）
        test_url = url.rstrip('/') + "/models"
        # 只要能在 timeout 内返回，哪怕是 401 报错都说明网络链路没有卡住
        requests.get(test_url, timeout=timeout)
        return True
    except Exception:
        return False

# ==========================================
# 1. 定义 Agent 状态模型 (State)
# ==========================================
class AgentState(TypedDict):
    # 输入上下文
    user_query: str                    # 用户最新输入
    location: str                      # 当前位置（如：深圳市）
    weather: str                       # 当前天气
    time: datetime.datetime            # 当前时间
    
    # 实体状态
    fridge_inventory: Dict[str, str]   # 冰箱已有食材
    diet_goal: str                     # 饮食健身目标
    avoid_ingredients: List[str]       # 忌口和过敏源
    
    # 决策流中间状态
    dining_mode: str                   # 模式：'Cook' (做饭) 或 'Delivery' (外卖)
    season: str                        # 季节
    seasonal_ingredients: List[str]    # 时令食材列表
    health_tips: str                   # 应季建议
    
    # 结果状态
    recommendations: List[Dict[str, Any]] # 推荐食谱/外卖列表
    selected_recipe: Optional[Dict[str, Any]] # 用户确认选定的食谱
    
    # 交互输出
    message: str                       # Agent 最终返回给用户的话语
    chat_history: List[BaseMessage]    # 对话历史
    save_memory: Optional[bool]        # 是否在本轮触发偏好记忆归档与提炼


# ==========================================
# 2. 编写 Graph 节点逻辑 (Nodes)
# ==========================================

def agent_node(state: AgentState) -> Dict[str, Any]:
    """
    智能大厨 Agent 决策节点 (ReAct)：
    1. 大模型扮演可爱热情的餐桌助理小厨娘。
    2. 基于用户的输入语句，自发调用 6 大工具进行信息查询或物理修改。
    3. 支持多轮对话流转，且通过局部的工具拦截，智能将扣减的菜谱对象同步为 selected_recipe。
    4. 自动提取并路由用餐模式、计算时令以及同步冰箱和忌口配置，完美向下兼容原有的状态流转 and 断言校验。
    """
    print("[Node] 执行 Agent 决策节点")
    user_input = state["user_query"].strip()
    current_time = state.get("time") or datetime.datetime.now()
    current_weather = state.get("weather") or "未知"
    
    # 优先读取用户画像中的常住城市，没有再降级到 State 中的 location 或默认值
    prof_init = profile_tool.get_profile()
    profile_city = prof_init.get("home_city", "").strip()
    current_city = profile_city if profile_city else (state.get("location") or "北京市")
    
    # 每次运行前清空上一轮扣减的缓存与检索历史
    state_container.matched_recipe_on_deduct = None
    state_container.last_searched_recipes = []
    
    # 针对“确认做某道菜”的输入进行主动拦截以保证 100% 的物理联动扣减（向下兼容测试和高可靠交互）
    q = user_input.lower()
    is_confirming_recipe = False
    target_recipe_name = None
    
    confirm_match = re.search(r"(?:就做|确认做|来做|做一做|煮|炒|煲)([^吧a-zA-Z0-9\s，。！🍳✨💖🥰]+)(?:吧|啦|项目)?", q)
    if confirm_match:
        target_recipe_name = confirm_match.group(1).strip()
        is_confirming_recipe = True
        
    if is_confirming_recipe and target_recipe_name:
        print(f"[Agent] 拦截到用户确认做菜指令: 菜名 = {target_recipe_name}")
        matched_rec = None
        if retriever:
            search_res = retriever.search(query=target_recipe_name, top_k=1)
            if search_res and target_recipe_name in search_res[0]["name"]:
                matched_rec = search_res[0]
        if matched_rec:
            try:
                deduct_fridge_ingredients.invoke({"ingredients": matched_rec["ingredients"], "recipe_name": matched_rec["name"]})
            except Exception as e:
                print(f"[Agent] 拦截扣减失败 ({str(e)})")
            state_container.matched_recipe_on_deduct = matched_rec
            
    # 1. 自动提取并路由用餐模式，满足原状态机中该字段的定义与测试用例 of 属性断言
    try:
        dining_mode = profile_tool.route_dining_mode(current_location=current_city, current_time=current_time)
    except Exception:
        dining_mode = "Cook"
        
    # 智能探测当前是否有下厨条件
    no_cook_keywords = [
        "在公司", "办公室", "没厨房", "没有厨房", "没火", "没有火", "没下厨条件", 
        "点外卖", "点个外卖", "吃外卖", "在外头", "在学校", "出差", "下馆子", 
        "不做饭", "不开火", "在途", "路上", "车上", "商场"
    ]
    query_text = user_input.lower()
    history_text_for_detect = query_text
    if state.get("chat_history"):
        for msg in state["chat_history"][-2:]:
            if isinstance(msg, HumanMessage):
                history_text_for_detect += " " + msg.content.lower()
                
    has_cooking_condition = True
    if dining_mode == "Delivery":
        has_cooking_condition = False
    elif any(kw in history_text_for_detect for kw in no_cook_keywords):
        has_cooking_condition = False
        
    # 2. 自动进行时令与天气 analysis
    try:
        analysis = context_tool.get_seasonal_analysis(city=current_city, date_obj=current_time.date(), weather=current_weather if current_weather != "未知" else "闷热多云")
        season = analysis.get("season", "四季")
        seasonal_ingredients = analysis.get("suggested_ingredients", [])
        health_tips = analysis.get("health_tips", "无")
    except Exception:
        season = "四季"
        seasonal_ingredients = []
        health_tips = "无特定时令养生建议。"
        
    # 3. 自动同步冰箱和画像配置
    try:
        fridge_inventory = inventory_tool.get_inventory()
    except Exception:
        fridge_inventory = {}
    try:
        prof = profile_tool.get_profile()
        diet_goal = prof.get("diet_goal", "")
        avoid_ingredients = prof.get("avoid_ingredients", [])
    except Exception:
        diet_goal = ""
        avoid_ingredients = []
        
    # 基础状态回写包
    result_update = {
        "dining_mode": dining_mode,
        "season": season,
        "seasonal_ingredients": seasonal_ingredients,
        "health_tips": health_tips,
        "fridge_inventory": fridge_inventory,
        "diet_goal": diet_goal,
        "avoid_ingredients": avoid_ingredients,
    }
    
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model_name = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
    
    use_fallback = not api_key
        
    if not use_fallback:
        try:
            # 大模型 ReAct 决策循环
            
            llm = ChatOpenAI(openai_api_key=api_key, openai_api_base=base_url, model_name=model_name, temperature=0.5, timeout=30.0, max_retries=0)
            llm_with_tools = llm.bind_tools(tools)
            
            # 构造 ReAct 系统人设
            messages = [
                SystemMessage(content="你是一个可爱、热情、超级懂美食和时令健康的智能餐桌小助理（小厨娘）。\n"
                                       "你可以根据需要随时调用工具来调取/修改配置画像、获取天气和时令、查看/增改/扣减冰箱食材、以及高精度检索食谱步骤。\n"
                                       "在与用户交流时，请保持非常热情活泼、有温度、多用 Emoji（如 🍳, ✨, 💖, 🥰）的助理大厨人设。\n"
                                       "\n"
                                       "【当前环境上下文】\n"
                                       f"当前系统时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                                       f"当前定位城市: {current_city}\n"
                                       f"当前实时天气: {current_weather}\n"
                                       f"当前餐饮路由模式: {dining_mode}\n"
                                       f"当前是否有下厨条件: {'是' if has_cooking_condition else '否（用户目前没有下厨条件/不在家/在公司/吃外卖/无厨房，绝对不要根据冰箱食材进行强行限制推荐！调用 search_recipes 时必须将 ingredients_must_have 设置为 None）'}\n"
                                       "\n"
                                       "【重要说明】\n"
                                       "1. 除非用户提到自己换了城市，或者当前实时天气未知，否则请直接使用上述【当前环境上下文】中的地理位置和天气信息，不要重复调用 get_weather_and_location 工具。\n"
                                       "2. 当用户想问“今天吃什么”时：请确保通过工具获取天气定位和冰箱，再调用 search_recipes 辅助决策。\n"
                                       "3. 当用户确认要制作某道菜时：请一定要调用 deduct_fridge_ingredients 物理扣减冰箱中的食材。请务必同时传入 ingredients 食材列表和 recipe_name（对应锁定食谱的具体菜名，如“青椒土豆炒肉”），并在对话中告知用户。\n"
                                       "4. 对话回复不要刻板拼接，请用可爱热情的口吻自由地组织大厨语言。\n"
                                       "5. 不要机械地重复天气信息：【当前环境上下文】中的实时天气、温度等背景数据仅用于后台决策（如根据下雨或冷热推荐暖胃或清爽的食物）。请绝对不要在每次回复中都刻意或格式化地向用户播报今天天气如何，除非用户主动问起，或者该天气是某个特定推荐极其关键的支撑理由。\n"
                                       "6. 绝对严格地从本地食谱数据库中选择 and 回答：你所推荐或提供做法步骤 of 每一道菜，必须是本地食谱数据库（即调用 search_recipes 工具返回的结果）中确凿存在的菜品。如果用户询问了本地食谱库中不存在的菜（或者 search_recipes 结果为空），你必须直白、抱歉地坦白“非常抱歉，我的本地食谱库里暂时没有收集这道菜的做法步骤”，绝对不能私自脑补、想象、手写或者“量身定制”任何本地数据库里没有的菜名和做法步骤！你可以基于冰箱现有食材重新调用 search_recipes 并主动推荐数据库中实际存在的替代菜。\n"
                                       "7. 严禁在非指令下主动推荐具体菜名：当用户没有明确要求你推荐菜品或询问食谱时（例如用户仅仅是查看冰箱库存、配置画像或进行普通打招呼闲聊），你绝对不能在回复中自作主张、画蛇添足地抛出或提及任何具体的菜名（如“番茄炒牛肉”、“青椒土豆丝”等），因为这可能引导用户去询问本地数据库中根本没有收录的菜。你应当仅老老实实、简明扼要地回答用户当前的提问（如列出冰箱里的食材清单），最后只需热情地询问用户是否需要帮您推荐菜品或查找做法即可，绝不要在没有成功运行 search_recipes 工具并返回有效结果的情况下擅自说出具体的菜谱名字。\n"
                                       "8. 智能识别下厨条件（做饭 vs 外卖/参考）：当前餐饮模式（dining_mode）在后台已被自动路由分析。如果 dining_mode 为 'Delivery'，或者用户通过对话明确表明自己不在家、在公司、没有下厨条件（如“在公司”、“没厨房”、“没火”、“点外卖”等）时，请绝对不要再根据冰箱食材强行限制推荐！在此种场景下调用 search_recipes 工具时，请将 ingredients_must_have 参数明确设为 None。此时大厨应当直接根据用户的口味描述在数据库中进行模糊或语义检索并直接推荐，无需与冰箱库存绑定。"),
            ]
            
            # 续接对话历史
            if state.get("chat_history"):
                messages.extend(state["chat_history"])
                
            messages.append(HumanMessage(content=user_input))
            
            # 最大允许调用 5 次工具链循环以防止死循环
            for _ in range(5):
                response = llm_with_tools.invoke(messages)
                messages.append(response)
                
                if not response.tool_calls:
                    break
                    
                # 依次调用工具
                for tool_call in response.tool_calls:
                    name = tool_call["name"]
                    args = tool_call["args"]
                    print(f"[Agent TUI] 大厨决定调用工具: {name} | 参数: {args}")
                    
                    # 防御过滤拦截：若无下厨条件，强制清除 search_recipes 中的 ingredients_must_have 限制
                    if name == "search_recipes" and not has_cooking_condition:
                        if args.get("ingredients_must_have"):
                            print(f"[Agent Defense] 检测到当前无下厨条件，强行将 search_recipes 的 ingredients_must_have 参数从 {args['ingredients_must_have']} 过滤为 None")
                            args["ingredients_must_have"] = None
                    
                    tool_func = tools_map.get(name)
                    if tool_func:
                        try:
                            res = tool_func.invoke(args)
                            if name == "get_weather_and_location":
                                city_match = re.search(r"当前城市定位:\s*([^\n]+)", res)
                                weather_match = re.search(r"当前实时天气:\s*([^\n]+)", res)
                                if city_match:
                                    current_city = city_match.group(1).strip()
                                if weather_match:
                                    current_weather = weather_match.group(1).strip()
                        except Exception as e:
                            res = f"工具执行异常: {str(e)}"
                    else:
                        res = f"未找到指定的工具: {name}"
                        
                    messages.append(ToolMessage(content=str(res), tool_call_id=tool_call["id"]))

            final_reply = messages[-1].content
            recommendations = state_container.last_searched_recipes if state_container.last_searched_recipes else []
            
            result_update.update({
                "message": final_reply,
                "recommendations": recommendations,
                "selected_recipe": state_container.matched_recipe_on_deduct,
                "chat_history": messages[1:],
                "location": current_city,
                "weather": current_weather
            })
            return result_update

        except Exception as e:
            print(f"[Agent] 大模型调用出现异常 ({str(e)})，正在退化到纯本地规则模式...")
            use_fallback = True

    if use_fallback:
        q = user_input.lower()
        res_recs = []
        is_asking_recipe_detail = any(x in q for x in ["做", "怎么", "做法", "步骤", "食谱", "怎么做", "配方"])
        
        r = state_container.matched_recipe_on_deduct
        if not r and retriever:
            search_name = None
            confirm_match = re.search(r"(?:就做|确认做|来做|做一做|煮|炒|煲)([^吧a-zA-Z0-9\s，。！🍳✨💖🥰]+)(?:吧|啦|项目)?", q)
            if confirm_match:
                search_name = confirm_match.group(1).strip()
            if not search_name:
                search_name = q
            search_res = retriever.search(query=search_name, top_k=1)
            if search_res:
                r = search_res[0]

        if is_asking_recipe_detail and r:
            msg = (
                f"小主！我已经帮您找出了本地食谱库中【{r['name']}】的配方啦！🍳\n"
                f"主要食材：{', '.join(r['ingredients'])}\n"
                f"做法步骤如下：\n" + "\n".join([f" - {step}" for step in r['steps']])
            )
            result_update.update({
                "message": msg,
                "recommendations": [r],
                "selected_recipe": r,
                "chat_history": state.get("chat_history", []) + [HumanMessage(content=user_input), AIMessage(content=msg)],
                "location": current_city,
                "weather": current_weather
            })
            return result_update

        # 根据下厨条件判断是否使用冰箱食材
        must_haves = list(fridge_inventory.keys()) if has_cooking_condition else None
        if retriever:
            if must_haves:
                res_recs = retriever.search(query="汤", ingredients_must_have=must_haves, top_k=1)
            if not res_recs:
                search_q = seasonal_ingredients[0] if seasonal_ingredients else "家常菜"
                # 如果没有下厨条件，优先检索用户 query
                if not has_cooking_condition:
                    res_recs = retriever.search(query=user_input, top_k=2)
                if not res_recs:
                    res_recs = retriever.search(query=search_q, top_k=1)

        if res_recs:
            if has_cooking_condition:
                msg = f"小厨娘发现您冰箱里有食材，强烈推荐您做这道【{res_recs[0]['name']}】！吃它吃它！🥰✨"
            else:
                msg = f"小厨娘给您推荐这道【{res_recs[0]['name']}】！虽然没有下厨条件，但在外面吃或者点外卖也是绝佳的选择哦！🥰✨"
            result_update.update({
                "message": msg,
                "recommendations": res_recs,
                "selected_recipe": None,
                "chat_history": state.get("chat_history", []) + [HumanMessage(content=user_input), AIMessage(content=msg)],
                "location": current_city,
                "weather": current_weather
            })
            return result_update
        else:
            msg = "嗨呀！我是您可爱热情的餐桌助手小厨娘！随时可以问我今天吃什么，或者问我某道菜 the 详细步骤哦！🍳💖"
            result_update.update({
                "message": msg,
                "recommendations": [],
                "selected_recipe": None,
                "chat_history": state.get("chat_history", []) + [HumanMessage(content=user_input), AIMessage(content=msg)],
                "location": current_city,
                "weather": current_weather
            })



def memory_node(state: AgentState) -> Dict[str, Any]:
    """
    记忆节点：仅在对话结束或指定提炼时触发。利用大模型分析本轮及整段历史对话，提炼饮食偏好与常住城市，持久化至 user_profile.json
    """
    # 只有当 state 显式指示需要提炼与保存时，或者有一些必须提炼的特征词时才触发
    if not state.get("save_memory"):
        print("[Memory] 跳过本轮记忆提炼（平常对话中不重复调用，只在会话退出或有保存信号时触发）。")
        return {}

    print("[Node] 执行 Memory 节点，自动提炼并压缩偏好记忆")
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[Memory] 未配置大模型 API 密钥，跳过记忆提炼。")
        return {}

    profile = profile_tool.get_profile()
    current_goal = profile.get("diet_goal", "")
    current_avoid = profile.get("avoid_ingredients", [])
    current_city = profile.get("home_city", "")

    user_input = state["user_query"].strip()
    recs_names = [r["name"] for r in state.get("recommendations", [])]
    selected_name = state["selected_recipe"]["name"] if state.get("selected_recipe") else "无"

    # 把对话历史转换为文本片段，进行通盘大总结
    history_list = []
    if state.get("chat_history"):
        for msg in state["chat_history"]:
            if isinstance(msg, HumanMessage):
                history_list.append(f"用户: {msg.content}")
            elif isinstance(msg, AIMessage):
                # 限制助手内容长度以防 Token 溢出，只取前 200 字
                content_snippet = msg.content[:200] + "..." if len(msg.content) > 200 else msg.content
                history_list.append(f"助手: {content_snippet}")
    
    # 加入最后一轮的输入和推荐
    history_list.append(f"用户: {user_input}")
    if recs_names:
        history_list.append(f"助手推荐候选: {', '.join(recs_names)}，最终选定: {selected_name}")
    
    history_text = "\n".join(history_list)

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model_name = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

    try:
        llm = ChatOpenAI(
            openai_api_key=api_key,
            openai_api_base=base_url,
            model_name=model_name,
            temperature=0.0,
            timeout=20.0,
            max_retries=0
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", "你是一个精密的饮食画像提炼与压缩专家。\n"
                       "请根据提供的一整段用户与助手的【全量会话历史】，分析、合并并提炼用户在整场对话中表达出的【长期饮食目标】、【特定食材忌口/过敏源】、以及【常住城市】。\n"
                       "合并规则：\n"
                       "1. avoid_ingredients: 提取用户显式说不吃、忌口、不要的配料食材，追加至原列表，去重保持精炼。\n"
                       "2. diet_goal: 只有在用户显式提到其新的健身目标时才修改，否则保持原样。\n"
                       "3. home_city: 如果用户在对话期间提到自己在哪个城市，就更新此字段；如果没有提到城市则保持原值。\n"
                       "原画像配置：\n"
                       "饮食目标: {current_goal}\n"
                       "避开食材: {current_avoid}\n"
                       "常住城市: {current_city}\n"
                       "请务必输出如下的纯 JSON 格式，不要包裹在 markdown 代码块中：\n"
                       "{{\n"
                       "  \"diet_goal\": \"更新后的目标，不需要则保持原值\",\n"
                       "  \"avoid_ingredients\": [\"食材1\", \"食材2\"],\n"
                       "  \"home_city\": \"城市名，没提到则保持原值\"\n"
                       "}}"),
            ("user", "【全量会话历史与推荐记录】\n{history_text}")
        ])

        parser = JsonOutputParser()
        chain = prompt | llm | parser

        res = chain.invoke({
            "current_goal": current_goal,
            "current_avoid": str(current_avoid),
            "current_city": current_city or "未设定",
            "history_text": history_text
        })

        new_goal = res.get("diet_goal")
        new_avoid = res.get("avoid_ingredients")
        new_city = res.get("home_city", "").strip()

        updated_profile = {}
        if new_goal is not None:
            updated_profile["diet_goal"] = new_goal
        if isinstance(new_avoid, list):
            updated_profile["avoid_ingredients"] = new_avoid
        # 只有用户显式说出城市才更新
        if new_city and new_city != "未设定" and new_city != current_city:
            updated_profile["home_city"] = new_city
            print(f"[Memory] 检测到城市变更: {current_city or '未设定'} -> {new_city}")

        if updated_profile:
            print(f"[Memory] 自动提取出偏好变动，正在更新用户画像: {updated_profile}")
            profile_tool.update_profile(updated_profile)
    except Exception as e:
        print(f"[Memory] 记忆自动压缩保存失败 ({str(e)})")

    return {}

# ==========================================
# 5. 组装极简 StateGraph (只有 Agent 和 Memory 两个节点)
# ==========================================
workflow = StateGraph(AgentState)

workflow.add_node("agent", agent_node)
workflow.add_node("memory", memory_node)

workflow.set_entry_point("agent")
workflow.add_edge("agent", "memory")
workflow.add_edge("memory", END)

app_graph = workflow.compile(checkpointer=MemorySaver())

# 本地调试
if __name__ == "__main__":
    test_state = {
        "user_query": "你知道我现在在哪吗？",
        "location": "成都市",
        "time": datetime.datetime.now(),
        "weather": "暴雨闷热",
        "chat_history": []
    }
    config = {"configurable": {"thread_id": "test_agent_thread"}}
    res = app_graph.invoke(test_state, config)
    print("\n[大厨最终回复]:")
    print(res["message"])
