import os
import json
from typing import Dict, Any, List, Optional
import datetime
from dotenv import load_dotenv

load_dotenv()

PROFILE_PATH = "data/user_profile.json"

DEFAULT_PROFILE = {
    "home_city": "",                      # 用户常住城市，优先于 IP 定位
    "diet_goal": "",                      # 默认无健身目标（普通饮食）
    "avoid_ingredients": [],              # 默认无忌口
    "workday_lunch_delivery": False,      # 默认不开启写字楼外卖路由，需用户手动勾选启用
    "office_locations": ["写字楼", "大厦", "园区", "科学园", "科技园", "中心", "工作室", "工位", "公司", "办公室"]  # 默认的常见写字楼及办公地名关键字
}

def init_profile_if_not_exists(profile_path: str = PROFILE_PATH):
    """初始化用户配置文件"""
    os.makedirs(os.path.dirname(profile_path), exist_ok=True)
    if not os.path.exists(profile_path):
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_PROFILE, f, ensure_ascii=False, indent=2)

class UserProfileTool:
    """用户个性化画像管理工具，支持读取、更新及用餐模式路由判定"""
    def __init__(self, profile_path: str = PROFILE_PATH):
        self.profile_path = profile_path
        init_profile_if_not_exists(self.profile_path)

    def get_profile(self) -> Dict[str, Any]:
        """获取用户画像配置"""
        try:
            with open(self.profile_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Profile] 读取画像失败 ({str(e)})，返回默认值。")
            return DEFAULT_PROFILE.copy()

    def update_profile(self, new_profile: Dict[str, Any]) -> Dict[str, Any]:
        """更新用户配置"""
        profile = self.get_profile()
        profile.update(new_profile)
        try:
            with open(self.profile_path, "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Profile] 写入画像失败: {str(e)}")
        return profile

    def is_office_location(self, location_name: str) -> bool:
        """根据当前定位地名判断是否为写字楼/办公地点"""
        profile = self.get_profile()
        office_keywords = profile.get("office_locations", DEFAULT_PROFILE["office_locations"])
        return any(keyword in location_name for keyword in office_keywords)

    def route_dining_mode(
        self,
        current_location: str,
        current_time: Optional[datetime.datetime] = None
    ) -> str:
        """
        核心路由决策：根据定位和时间，自动判断是做饭模式还是外卖模式
        :param current_location: 用户当前定位名称或城市名，如“科技园大厦”或“幸福小区（家）”
        :param current_time: 指定时间，便于测试。若为空则采用当前系统时间。
        :return: "Cook" (做饭模式) 或 "Delivery" (外卖模式)
        """
        profile = self.get_profile()
        
        # 1. 检查是否启用了“工作日中午默认点外卖”规则
        if not profile.get("workday_lunch_delivery", False):
            return "Cook" # 未启用则默认推荐做饭（家常推荐）
            
        if not current_time:
            current_time = datetime.datetime.now()

        # 2. 判断是否是工作日 (周一到周五为 0-4)
        is_workday = current_time.weekday() < 5
        
        # 3. 判断是否是中午时间段 (通常为 11:00 ~ 13:30)
        is_lunch_time = 11 <= current_time.hour <= 13
        
        # 4. 判断是否是写字楼/办公场所
        is_office = self.is_office_location(current_location)

        # 5. 命中规则：如果是工作日、处于午饭时间、且人在写字楼办公区，自动切换为外卖模式
        if is_workday and is_lunch_time and is_office:
            return "Delivery"

        return "Cook"

# 本地调试代码
if __name__ == "__main__":
    tool = UserProfileTool(profile_path="data/user_profile_test.json")
    print("默认配置:", tool.get_profile())
    
    # 模拟工作日中午在写字楼
    test_time_1 = datetime.datetime(2026, 6, 8, 12, 15) # 周一中午
    mode_1 = tool.route_dining_mode(current_location="南山科技园大厦C栋", current_time=test_time_1)
    print(f"测试1 (周一中午，科技园写字楼) 路由结果: {mode_1} (预期: Delivery)")
    
    # 模拟周末晚上在小区
    test_time_2 = datetime.datetime(2026, 6, 7, 18, 30) # 周日晚上
    mode_2 = tool.route_dining_mode(current_location="幸福里小区", current_time=test_time_2)
    print(f"测试2 (周日晚上，住宅小区) 路由结果: {mode_2} (预期: Cook)")
