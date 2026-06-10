import os
import sys
import datetime
import uuid
from dotenv import load_dotenv

# 将项目根目录加入模块搜索路径，确保可以直接导入 src
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 强制标准输出为 UTF-8，解决 Windows GBK 终端乱码/崩溃问题
# 必须在任何 print 之前执行
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.rag.vector_store import RecipeRetriever
from src.tools.inventory_tool import InventoryTool
from src.tools.context_tool import ContextTool
from src.tools.user_profile_tool import UserProfileTool
from src.agent.graph import app_graph

# 加载配置
load_dotenv()

# ANSI 控制台色彩代码，用于打造 Premium 彩色终端 TUI 体验
COLOR_HEADER = "\033[95m"    # 紫色 (系统头部)
COLOR_AGENT = "\033[96m"     # 青色 (大厨)
COLOR_USER = "\033[92m"      # 绿色 (用户)
COLOR_WARN = "\033[93m"      # 黄色 (警告)
COLOR_ERROR = "\033[91m"     # 红色 (错误)
COLOR_HIGHLIGHT = "\033[97m" # 白色高亮
COLOR_RESET = "\033[0m"       # 恢复默认


def print_divider():
    print(f"{COLOR_HEADER}================================================================================{COLOR_RESET}")


def print_chef(message: str):
    """大厨角色打印函数"""
    print(f"\n{COLOR_AGENT}[大厨] >{COLOR_RESET}")
    for line in message.split("\n"):
        print(f"  {line}")
    print()


def print_system(message: str):
    """系统级别打印函数"""
    print(f"{COLOR_HEADER}[系统] {message}{COLOR_RESET}")


def print_warning(message: str):
    """警告级别打印函数"""
    print(f"{COLOR_WARN}[警告] {message}{COLOR_RESET}")


def show_help():
    print(f"\n{COLOR_HIGHLIGHT}[提示] 终端大厨可用指令集：{COLOR_RESET}")
    print(f"  {COLOR_USER}/fridge{COLOR_RESET}           - 查看冰箱当前库存食材")
    print(f"  {COLOR_USER}/profile{COLOR_RESET}          - 查看当前用户的口味偏好与忌口配置")
    print(f"  {COLOR_USER}/clear{COLOR_RESET}            - 一键清空冰箱内的所有食材")
    print(f"  {COLOR_USER}/location [城市]{COLOR_RESET}  - 手动更改当前地理定位（如：/location 广州市）")
    print(f"  {COLOR_USER}/help{COLOR_RESET}             - 显示此帮助命令列表")
    print(f"  {COLOR_USER}/exit{COLOR_RESET}             - 退出并告别终端大厨")
    print(f"  {COLOR_HIGHLIGHT}直接输入对话语句{COLOR_RESET}（例如：我想用排骨做个汤）即可开始聊天。\n")


def run_tui():
    # 强制在 Windows 终端中启用 ANSI 转义码支持
    if os.name == 'nt':
        os.system('color')

    print_divider()
    print(f"{COLOR_HEADER}          Chef-RAG  智能餐桌终端版 (TUI App){COLOR_RESET}")
    print(f"{COLOR_HEADER}    结合冰箱库存 + 时令天气分析 + 用户画像的智能点餐/做饭决策助手{COLOR_RESET}")
    print_divider()

    # 初始化底层工具
    inventory_tool = InventoryTool()
    context_tool = ContextTool()
    profile_tool = UserProfileTool()

    # 检测并警告 API Key 缺失
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print_warning("未检测到 OPENAI_API_KEY 环境变量！系统将降级运行在纯本地规则检索模式中。")

    # 自动获取环境定位与天气
    prof = profile_tool.get_profile()
    profile_city = prof.get("home_city", "").strip()
    if profile_city:
        current_city = profile_city
        print_system(f"当前常住定位 (画像配置): {COLOR_HIGHLIGHT}{current_city}{COLOR_RESET}")
    else:
        loc_info = context_tool.get_current_location()
        current_city = loc_info.get("city", "未知城市")
        print_system(f"当前自适应定位: {COLOR_HIGHLIGHT}{current_city}{COLOR_RESET}")
    current_time = datetime.datetime.now()
    current_weather = context_tool.get_real_weather(current_city)
    print_system(f"当前系统时间段: {COLOR_HIGHLIGHT}{current_time.strftime('%Y-%m-%d %H:%M')}{COLOR_RESET}")
    if current_weather != "未知":
        print_system(f"当前实时天气:   {COLOR_HIGHLIGHT}{current_weather}{COLOR_RESET}")
    else:
        print_system(f"当前实时天气:   {COLOR_WARN}未获取到（可配置 QWEATHER_API_KEY 启用）{COLOR_RESET}")


    # 初始化 LangGraph thread_id 保持多轮对话一致性
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    show_help()

    # 第一次建立连接状态
    is_first_turn = True

    # 输入循环 (REPL)
    while True:
        try:
            user_input = input(f"{COLOR_USER}用户 > {COLOR_RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            print_system("正在帮您自动提炼并压缩本轮会话的偏好记忆，请稍候...")
            try:
                app_graph.invoke({
                    "user_query": "退出程序，整理全量会话画像并保存配置",
                    "save_memory": True
                }, config)
            except Exception:
                pass
            print(f"{COLOR_HEADER}[再见] 感谢使用 Chef-RAG 智能餐桌！祝您用餐愉快！{COLOR_RESET}")
            break

        if not user_input:
            continue

        # 解析命令
        cmd = user_input.lower()
        if cmd == "/exit":
            print_system("正在帮您自动提炼并压缩本轮会话的偏好记忆，请稍候...")
            try:
                app_graph.invoke({
                    "user_query": "退出程序，整理全量会话画像并保存配置",
                    "save_memory": True
                }, config)
            except Exception:
                pass
            print(f"{COLOR_HEADER}[再见] 感谢使用 Chef-RAG 智能餐桌！祝您用餐愉快！{COLOR_RESET}")
            break
        elif cmd == "/help":
            show_help()
            continue
        elif cmd == "/fridge":
            fridge = inventory_tool.get_inventory()
            print_divider()
            print(f"{COLOR_HIGHLIGHT}[冰箱] 当前冷藏库存：{COLOR_RESET}")
            if not fridge:
                print("  (当前冰箱空空如也，可以直接输入文字添加食材，例如：我买了鸡蛋和西红柿)")
            else:
                for k, v in fridge.items():
                    print(f"  * {COLOR_HIGHLIGHT}{k}{COLOR_RESET}: {v if v else '若干'}")
            print_divider()
            continue
        elif cmd == "/profile":
            prof = profile_tool.get_profile()
            print_divider()
            print(f"{COLOR_HIGHLIGHT}[画像] 用户画像配置详情：{COLOR_RESET}")
            print(f"  * 长期饮食目标: {COLOR_HIGHLIGHT}{prof.get('diet_goal', '普通饮食') or '无特定'}{COLOR_RESET}")
            avoid_list = prof.get('avoid_ingredients', [])
            print(f"  * 避开食材/过敏源: {COLOR_HIGHLIGHT}{', '.join(avoid_list) if avoid_list else '无'}{COLOR_RESET}")
            print(f"  * 写字楼午餐外卖路由: {COLOR_HIGHLIGHT}{'已开启' if prof.get('workday_lunch_delivery') else '已关闭'}{COLOR_RESET}")
            office_locs = prof.get('office_locations', [])
            print(f"  * 预设办公场所关键字: {COLOR_HIGHLIGHT}{', '.join(office_locs) if office_locs else '无'}{COLOR_RESET}")
            print_divider()
            continue
        elif cmd == "/clear":
            inventory_tool.clear_inventory()
            print_system("冰箱库存已成功一键清空！")
            continue
        elif user_input.startswith("/location "):
            new_loc = user_input[len("/location "):].strip()
            if new_loc:
                current_city = new_loc
                is_first_turn = True
                print_system(f"当前地理定位已成功修改为: {COLOR_HIGHLIGHT}{current_city}{COLOR_RESET}")
            else:
                print_warning("请输入具体城市名称，例如：/location 深圳市")
            continue
        elif user_input.startswith("/"):
            print_warning(f"未知指令 '{user_input}'，输入 {COLOR_HIGHLIGHT}/help{COLOR_RESET} 获取可用指令列表。")
            continue

        # 输入普通对话，调用大模型决策
        print_system("大脑思考中，请稍候...")
        try:
            if is_first_turn:
                state_input = {
                    "user_query": user_input,
                    "location": current_city,
                    "time": current_time,
                    "weather": current_weather,
                    "chat_history": []
                }
                is_first_turn = False
            else:
                state_input = {
                    "user_query": user_input
                }

            # 调用图流转
            result_state = app_graph.invoke(state_input, config)

            # 打印回复
            print_chef(result_state.get("message", "未获得有效决策。"))

            # 若触发了食谱选定
            if result_state.get("selected_recipe") is not None:
                print_system(f"[完成] 已帮您锁定了食谱: {COLOR_HIGHLIGHT}{result_state['selected_recipe']['name']}{COLOR_RESET}")
                fridge = inventory_tool.get_inventory()
                print(f"  已自动消耗库存食材，冰箱最新剩余：{COLOR_HIGHLIGHT}{', '.join(fridge.keys()) if fridge else '已全部清空'}{COLOR_RESET}")

        except Exception as e:
            print(f"\n{COLOR_ERROR}[错误] 执行图流转决策失败: {str(e)}{COLOR_RESET}\n")


if __name__ == "__main__":
    run_tui()
