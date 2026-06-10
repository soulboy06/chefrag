import os
import sys
import datetime

# 将项目根目录加入模块搜索路径，确保可以直接导入 src
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.rag.vector_store import RecipeRetriever
from src.tools.inventory_tool import InventoryTool
from src.tools.context_tool import ContextTool
from src.tools.user_profile_tool import UserProfileTool
from src.agent.graph import app_graph

def safe_print(text: str):
    """安全打印函数，防范 Windows 终端 (GBK) 打印 Emoji 时引发的 Unicode 编码错误"""
    enc = sys.stdout.encoding or 'utf-8'
    print(text.encode(enc, errors='replace').decode(enc))

def run_tests():
    print("==========================================")
    # 强制清空测试环境下的 DB path 和 JSON，以防干扰
    test_fridge_path = "data/fridge_test_run.json"
    test_profile_path = "data/user_profile_test_run.json"
    test_recipes_path = "data/recipes_test_run.json"
    
    api_key_backup = os.getenv("OPENAI_API_KEY")
    
    # 用 Monkey Patch 动态将 Agent Graph 里的工具路径和食谱路径指向测试临时文件，实现完全的数据隔离
    import json
    from src.agent.graph import inventory_tool as graph_inventory_tool
    from src.agent.graph import profile_tool as graph_profile_tool
    from src.agent.graph import retriever as graph_retriever
    
    graph_inventory_tool.fridge_path = test_fridge_path
    graph_profile_tool.profile_path = test_profile_path
    
    if os.path.exists(test_fridge_path):
        os.remove(test_fridge_path)
    if os.path.exists(test_profile_path):
        os.remove(test_profile_path)
    if os.path.exists(test_recipes_path):
        os.remove(test_recipes_path)
        
    if graph_retriever:
        graph_retriever.recipes_path = test_recipes_path
        # 预设极简的测试专用食谱数据，支持过滤与时令检索测试
        test_recipes = [
            {
                "id": "recipe_001",
                "name": "番茄炒蛋",
                "ingredients": ["鸡蛋", "西红柿", "大葱"],
                "steps": ["把鸡蛋打散炒好", "炒番茄出汁", "混合起锅"],
                "tags": ["快手菜", "家常菜"],
                "cooking_time_minutes": 10,
                "calories": 200,
                "season": "四季"
            },
            {
                "id": "recipe_002",
                "name": "冬瓜排骨汤",
                "ingredients": ["排骨", "冬瓜", "小葱"],
                "steps": ["焯水后下砂锅", "大火烧开转小火慢炖50分钟", "加入冬瓜慢炖20分钟"],
                "tags": ["消暑", "煲汤"],
                "cooking_time_minutes": 60,
                "calories": 300,
                "season": "夏季"
            }
        ]
        os.makedirs(os.path.dirname(test_recipes_path), exist_ok=True)
        with open(test_recipes_path, "w", encoding="utf-8") as f:
            json.dump(test_recipes, f, ensure_ascii=False, indent=2)
            
        graph_retriever._load_recipes()
        graph_retriever._init_bm25()
        
    print("[Test 1] 测试冰箱管理工具...")
    inv_tool = InventoryTool(fridge_path=test_fridge_path)
    # 添加食材
    inv_tool.update_inventory({"排骨": "500g", "鸡蛋": "6个"}, merge=False)
    current_inv = inv_tool.get_inventory()
    assert "排骨" in current_inv, "排骨未成功添加到冰箱"
    assert current_inv["鸡蛋"] == "6个", "鸡蛋数量不对"
    
    # 消耗食材
    inv_tool.deduct_ingredients({"排骨": ""})
    current_inv = inv_tool.get_inventory()
    assert "排骨" not in current_inv, "排骨未被消耗扣减"
    assert "鸡蛋" in current_inv, "鸡蛋不应被消耗"
    print("[PASS] 冰箱管理工具测试通过！")
    
    print("\n[Test 2] 测试定位与时令分析工具...")
    ctx_tool = ContextTool()
    loc = ctx_tool.get_current_location()
    print(f"当前解析定位: {loc}")
    assert "city" in loc, "定位未包含城市字段"
    
    if api_key_backup:
        # 获取6月份广州的夏季时令分析
        analysis = ctx_tool.get_seasonal_analysis(city="广州市", date_obj=datetime.date(2026, 6, 15), weather="闷热")
        print(f"夏季时令食材: {analysis['suggested_ingredients']}")
        print(f"养生贴士: {analysis['health_tips']}")
        assert "冬瓜" in analysis["suggested_ingredients"] or "苦瓜" in analysis["suggested_ingredients"], "夏季时令食材推荐未命中瓜类"
        print("[PASS] 定位与时令工具测试通过！")
    else:
        print("[SKIP] 未配置 API_KEY，跳过大模型时令分析测试。")
    
    print("\n[Test 3] 测试用户画像与用餐模式路由决策...")
    prof_tool = UserProfileTool(profile_path=test_profile_path)
    # 模拟用户开启了“工作日中午人在写字楼默认外卖”规则，并手动配置了常用办公地点关键词
    prof_tool.update_profile({
        "workday_lunch_delivery": True,
        "office_locations": ["科学园"]
    })
    
    # 测试工作日中午写字楼的自动外卖路由
    lunch_time = datetime.datetime(2026, 6, 8, 12, 30) # 周一中午
    mode_delivery = prof_tool.route_dining_mode(current_location="深圳科兴科学园", current_time=lunch_time)
    assert mode_delivery == "Delivery", "工作日中午写字楼应判定为外卖模式"
    
    # 测试周末晚上小区的做饭路由
    dinner_time = datetime.datetime(2026, 6, 7, 18, 00) # 周日晚上
    mode_cook = prof_tool.route_dining_mode(current_location="碧桂园小区", current_time=dinner_time)
    assert mode_cook == "Cook", "周末晚上住宅区应判定为做饭模式"
    print("[PASS] 用户画像与路由工具测试通过！")
    
    print("\n[Test 4] 测试 RAG 混合检索过滤...")
    # 这里我们采用纯本地 BM25 模式做无 Key 测试
    # 清除环境变量 OPENAI_API_KEY 以测试降级
    os.environ["OPENAI_API_KEY"] = ""
    retriever = RecipeRetriever(recipes_path=test_recipes_path, persist_directory="data/chroma_db_test_run")
    
    # 模拟做饭模式：冰箱有排骨，要求在夏秋季做汤
    results = retriever.search(
        query="我想喝热汤",
        ingredients_must_have=["排骨"],
        season_filters=["夏季", "通用"]
    )
    print("做饭模式推荐结果:")
    for r in results:
        print(f" - {r['name']} (时令: {r['season']}, 缺失食材: {r.get('missing_ingredients', [])})")
    
    # 应优先推荐冬瓜排骨汤（因为有排骨且夏季时令加权）
    assert len(results) > 0, "推荐列表不能为空"
    assert results[0]["name"] == "冬瓜排骨汤", "首推应该为夏季时令冬瓜排骨汤"
    print("[PASS] RAG 混合检索过滤测试通过！")
    
    # 恢复环境变量以便后续有 Key 的集成测试能够运行
    if api_key_backup:
        os.environ["OPENAI_API_KEY"] = api_key_backup
    
    if api_key_backup:
        print("\n[Test 5] 测试 LangGraph 状态图全生命周期流转...")
        # 测试周末晚上在家做饭流
        initial_state = {
            "user_query": "今天晚上吃什么？我想喝汤",
            "location": "龙湖花园小区",
            "time": datetime.datetime(2026, 6, 14, 18, 30), # 周日晚上
            "weather": "暴雨，闷热",
            "chat_history": []
        }
        
        # 初始化测试冰箱，放入排骨
        inv_tool.update_inventory({"排骨": "500g"}, merge=False)
        
        config = {"configurable": {"thread_id": "test_thread_999"}}
        output_state = app_graph.invoke(initial_state, config)
        
        print(f"决策模式: {output_state['dining_mode']}")
        print(f"时令食材: {output_state['seasonal_ingredients']}")
        safe_print(f"大厨回复内容:\n{output_state['message']}")
        
        assert output_state["dining_mode"] == "Cook", "应路由为做饭模式"
        assert len(output_state["recommendations"]) > 0, "应有推荐列表"
        
        # 模拟用户输入“我确认做冬瓜排骨汤”
        follow_up = output_state.copy()
        follow_up["user_query"] = "就做冬瓜排骨汤吧"
        final_output = app_graph.invoke(follow_up, config)
        
        safe_print(f"\n用户确认后回复内容:\n{final_output['message']}")
        assert final_output["selected_recipe"] is not None, "最终应成功选定食谱"
        assert "排骨" not in inv_tool.get_inventory(), "确认做菜后冰箱排骨应被成功消耗"
        print("[PASS] LangGraph 全生命周期集成测试通过！")
    else:
        print("\n[Test 5] 未配置 OPENAI_API_KEY，已跳过需要联网大模型交互的图流转集成测试。")

    print("\n[Test 6] 测试无下厨条件（在公司/吃外卖）下的食谱推荐排除冰箱限制...")
    # 1. 测试 fallback 模式下无下厨条件排除冰箱限制
    os.environ["OPENAI_API_KEY"] = "" # 临时置空
    inv_tool.update_inventory({"排骨": "500g"}, merge=False)
    
    state_company_fallback = {
        "user_query": "我现在在公司呢，我想吃番茄炒蛋", # 明确表达在公司且想吃番茄炒蛋
        "location": "龙湖花园小区",
        "time": datetime.datetime(2026, 6, 14, 18, 30),
        "weather": "暴雨，闷热",
        "chat_history": []
    }
    
    config_6 = {"configurable": {"thread_id": "test_thread_666"}}
    output_6_fallback = app_graph.invoke(state_company_fallback, config_6)
    
    recs_6_fallback = [r["name"] for r in output_6_fallback["recommendations"]]
    print(f"在公司（fallback）推荐列表: {recs_6_fallback}")
    assert "番茄炒蛋" in recs_6_fallback, "无下厨条件下应能成功推荐非冰箱食材菜品（番茄炒蛋）"
    assert "虽然没有下厨条件" in output_6_fallback["message"], "话术应包含无下厨条件的提示"
    
    # 2. 如果有 API Key，测试 LLM 模式下是否也生效
    if api_key_backup:
        os.environ["OPENAI_API_KEY"] = api_key_backup
        state_company_llm = {
            "user_query": "我现在在公司呢，我想吃番茄炒蛋",
            "location": "龙湖花园小区",
            "time": datetime.datetime(2026, 6, 14, 18, 30),
            "weather": "暴雨，闷热",
            "chat_history": []
        }
        output_6_llm = app_graph.invoke(state_company_llm, config_6)
        recs_6_llm = [r["name"] for r in output_6_llm["recommendations"]]
        print(f"在公司（LLM）推荐列表: {recs_6_llm}")
        assert "番茄炒蛋" in recs_6_llm, "LLM模式下无下厨条件应能成功推荐非冰箱食材菜品（番茄炒蛋）"
            
    # 恢复环境变量
    if api_key_backup:
        os.environ["OPENAI_API_KEY"] = api_key_backup
    
    # 清理测试跑完产生的文件
    if os.path.exists(test_fridge_path):
        os.remove(test_fridge_path)
    if os.path.exists(test_profile_path):
        os.remove(test_profile_path)
    if os.path.exists(test_recipes_path):
        os.remove(test_recipes_path)
    print("\n==========================================")
    print("[SUCCESS] 所有可用测试用例全部通过！系统健壮性完美！")
    print("==========================================")

if __name__ == "__main__":
    run_tests()
