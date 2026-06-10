import re
import datetime
from typing import Dict, Any, List, Optional, Any as AnyType
from langchain_core.tools import tool

from src.tools.inventory_tool import InventoryTool
from src.tools.context_tool import ContextTool
from src.tools.user_profile_tool import UserProfileTool
from src.rag.vector_store import RecipeRetriever

# 保持与原有系统完全兼容的单例实例化
inventory_tool = InventoryTool()
context_tool = ContextTool()
profile_tool = UserProfileTool()

try:
    retriever = RecipeRetriever()
except Exception as e:
    print(f"[Tools] 初始化检索器失败: {str(e)}。")
    retriever = None

class ToolSharedState:
    def __init__(self):
        # 暂存本轮检索出的完整食谱字典，辅助扣减时还原 selected_recipe
        self.last_searched_recipes = []
        # 记录扣减产生的选定食谱
        self.matched_recipe_on_deduct = None

state_container = ToolSharedState()

# ==========================================
# 声明被 @tool 包装的 LangChain 接口
# ==========================================

@tool
def get_user_profile() -> str:
    """获取用户的个人画像配置，包括常住城市、长期饮食健身目标、忌口与过敏源。"""
    prof = profile_tool.get_profile()
    home_city = prof.get('home_city', '') or ''
    return (
        f"常住城市: {home_city if home_city else '未设定（自动 IP 定位）'}\n"
        f"长期饮食目标: {prof.get('diet_goal', '无特定') or '无特定'}\n"
        f"忌口与过敏源: {', '.join(prof.get('avoid_ingredients', [])) if prof.get('avoid_ingredients') else '无'}"
    )

@tool
def update_user_profile(diet_goal: Optional[str] = None, avoid_ingredients: Optional[List[str]] = None, home_city: Optional[str] = None) -> str:
    """更新用户的个人画像配置。参数均为可选，传入需要修改的内容即可。
    - diet_goal: 长期饮食目标
    - avoid_ingredients: 忌口食材/过敏源列表
    - home_city: 用户常住城市（如“宜宾市”），保存后重启也会自动使用
    """
    updates = {}
    if diet_goal is not None:
        updates["diet_goal"] = diet_goal
    if avoid_ingredients is not None:
        updates["avoid_ingredients"] = avoid_ingredients
    if home_city is not None and home_city.strip():
        updates["home_city"] = home_city.strip()
    res = profile_tool.update_profile(updates)
    return f"画像配置修改成功！当前最新配置: {res}"

@tool
def get_fridge_inventory() -> str:
    """查看冰箱当前拥有的食材和数量。"""
    fridge = inventory_tool.get_inventory()
    if not fridge:
        return "冰箱当前空空如也。"
    items_str = ", ".join([f"{k}: {v if v else '若干'}" for k, v in fridge.items()])
    return f"冰箱当前食材有: {items_str}"

@tool
def update_fridge_inventory(items: Dict[str, str]) -> str:
    """向冰箱中添加、修改食材。参数items为包含食材和对应数量的字典，例如 {'鸡蛋': '6个', '排骨': '500g'}。如果某样食材数量设置为空字符串或'0'，表示从冰箱中删除该食材。"""
    res = inventory_tool.update_inventory(items, merge=True)
    return f"冰箱食材修改成功！当前最新冰箱剩余食材为: {res}"

@tool
def deduct_fridge_ingredients(ingredients: List[str], recipe_name: Optional[str] = None) -> str:
    """当确定要制作某道菜时，将确认用到的食材从冰箱库存中物理扣减。
    参数说明：
    - ingredients: 需要扣除的食材名字列表，例如 ['排骨', '冬瓜']
    - recipe_name: 可选，当前确认要制作的菜谱名称，例如 '青椒土豆炒肉'。传入此参数可确保精确锁定该菜谱并物理联动。
    """
    res = inventory_tool.deduct_ingredients({ing: "" for ing in ingredients})
    
    matched = None
    if recipe_name and state_container.last_searched_recipes:
        clean_name = recipe_name.strip()
        for r in state_container.last_searched_recipes:
            if clean_name == r["name"] or clean_name in r["name"] or r["name"] in clean_name:
                matched = r
                break
                
    if not matched and state_container.last_searched_recipes:
        for r in state_container.last_searched_recipes:
            if set(r["ingredients"]) & set(ingredients):
                matched = r
                break
                
    if not matched and state_container.last_searched_recipes:
        matched = state_container.last_searched_recipes[0]
        
    if matched:
        state_container.matched_recipe_on_deduct = matched
            
    return f"已成功从冰箱中扣减了食材 {ingredients}，冰箱最新剩余: {list(res.keys())}"

@tool
def get_weather_and_location(city: Optional[str] = None) -> str:
    """获取当前的地理定位、系统日期、实时天气，以及对应的时令应季建议和时令推荐食材。参数city可选，若为空则优先读用户画像里的常住城市，再降级到 IP 定位。"""
    prof = profile_tool.get_profile()
    profile_city = prof.get("home_city", "").strip()

    if city:
        target_city = city
    elif profile_city:
        target_city = profile_city
    else:
        loc_info = context_tool.get_current_location()
        target_city = loc_info.get("city", "北京市")

    today = datetime.date.today()
    real_weather = context_tool.get_real_weather(target_city)
    analysis = context_tool.get_seasonal_analysis(city=target_city, date_obj=today, weather=real_weather)
    return (
        f"当前城市定位: {target_city}\n"
        f"当前系统日期: {today.strftime('%Y-%m-%d')}\n"
        f"当前实时天气: {real_weather}\n"
        f"当前季节: {analysis.get('season', '四季')}\n"
        f"时令饮食养生建议: {analysis.get('health_tips', '无')}\n"
        f"本季节推荐的当地时令主打食材: {', '.join(analysis.get('suggested_ingredients', []))}"
    )

@tool
def search_recipes(query: str, ingredients_must_have: Optional[AnyType] = None, ingredients_avoid: Optional[AnyType] = None) -> str:
    """
    从本地食谱数据库中执行高精度检索（BM25 + 向量检索），找出符合条件的家常菜谱及详细步骤。
    参数说明：
    - query: 检索关键字（如 '汤', '清淡'，或者是想要寻找的具体菜名，如 '茶叶蛋'）
    - ingredients_must_have: 必须包含的冰箱食材列表，支持 Python 列表或 JSON 数组字符串，多用于做饭模式下的精准库存匹配
    - ingredients_avoid: 必须避开的忌口过敏原列表，支持 Python 列表或 JSON 数组字符串
    召回的每道菜都会提供对应的 source_file（相对路径）、ingredients（食材列表）和 steps（做法详细步骤）。
    """
    if not retriever:
        return "本地检索系统未初始化成功。"
        
    def _parse_list(val) -> Optional[List[str]]:
        if val is None:
            return None
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            val = val.strip()
            if not val or val.lower() in ('none', 'null', '[]'):
                return None
            try:
                import json
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
            cleaned = val.replace('[', '').replace(']', '').replace('"', '').replace("'", '').strip()
            if not cleaned:
                return None
            return [x.strip() for x in cleaned.split(',') if x.strip()]
        return None

    parsed_must = _parse_list(ingredients_must_have)
    parsed_avoid = _parse_list(ingredients_avoid)
        
    results = retriever.search(
        query=query,
        ingredients_must_have=parsed_must,
        ingredients_avoid=parsed_avoid,
        top_k=3
    )
    if not results:
        return "抱歉，在本地食谱库中没有匹配到任何结果。"
        
    existing_ids = {r["id"] for r in state_container.last_searched_recipes}
    for r in results:
        if r["id"] not in existing_ids:
            state_container.last_searched_recipes.append(r)
    
    outputs = []
    for idx, r in enumerate(results):
        recipe_str = (
            f"候选 [{idx+1}] 菜谱详情：\n"
            f"菜名: {r['name']}\n"
            f"配料需求: {', '.join(r['ingredients'])}\n"
            f"操作步骤:\n" + "\n".join([f"  {i+1}. {step}" for i, step in enumerate(r['steps'])]) + "\n"
            f"物理溯源文件: {r.get('source_file', '未知')}\n"
        )
        outputs.append(recipe_str)
    return "\n---\n".join(outputs)

tools = [
    get_user_profile, 
    update_user_profile, 
    get_fridge_inventory, 
    update_fridge_inventory, 
    deduct_fridge_ingredients, 
    get_weather_and_location, 
    search_recipes
]
tools_map = {t.name: t for t in tools}
