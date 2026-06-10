import os
import json
from typing import Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from dotenv import load_dotenv

load_dotenv()

FRIDGE_PATH = "data/fridge.json"

def init_fridge_if_not_exists(fridge_path: str = FRIDGE_PATH):
    """初始化冰箱数据文件"""
    os.makedirs(os.path.dirname(fridge_path), exist_ok=True)
    if not os.path.exists(fridge_path):
        with open(fridge_path, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)

class InventoryTool:
    """冰箱食材管理工具，支持 CRUD 操作与大模型文本智能提取录入"""
    def __init__(self, fridge_path: str = FRIDGE_PATH):
        self.fridge_path = fridge_path
        init_fridge_if_not_exists(self.fridge_path)

    def get_inventory(self) -> Dict[str, str]:
        """获取当前冰箱的所有食材及其数量"""
        try:
            with open(self.fridge_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Inventory] 读取库存失败: {str(e)}")
            return {}

    def update_inventory(self, items: Dict[str, str], merge: bool = True) -> Dict[str, str]:
        """
        更新冰箱食材
        :param items: 需要更新的食材字典，如 {"西红柿": "3个", "排骨": "2斤"}
        :param merge: True 表示累加数量或合并，False 表示完全覆盖
        """
        inventory = self.get_inventory()
        if not merge:
            inventory = items
        else:
            for name, qty in items.items():
                if qty == "0" or qty == "" or qty is None:
                    # 如果数量为0或空，代表删除该食材
                    inventory.pop(name, None)
                else:
                    # 简单覆盖或累加（此处为简化，同名直接更新为最新输入的数量）
                    inventory[name] = qty
        
        try:
            with open(self.fridge_path, "w", encoding="utf-8") as f:
                json.dump(inventory, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Inventory] 写入库存失败: {str(e)}")
            
        return inventory

    def remove_item(self, item_name: str) -> Dict[str, str]:
        """删除某样食材"""
        inventory = self.get_inventory()
        inventory.pop(item_name, None)
        try:
            with open(self.fridge_path, "w", encoding="utf-8") as f:
                json.dump(inventory, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Inventory] 删除食材失败: {str(e)}")
        return inventory

    def clear_inventory(self) -> Dict[str, str]:
        """清空冰箱"""
        try:
            with open(self.fridge_path, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Inventory] 清空库存失败: {str(e)}")
        return {}

    def deduct_ingredients(self, used_items: Dict[str, str]) -> Dict[str, str]:
        """
        消耗/扣减食材（用于最终确认做菜后的闭环）
        例如做番茄炒蛋消耗 {"西红柿": "2个", "鸡蛋": "3个"}
        """
        import re
        inventory = self.get_inventory()
        for name, _ in used_items.items():
            if name in inventory:
                qty_str = inventory[name]
                if not qty_str:
                    inventory.pop(name)
                    continue
                qty_str = qty_str.strip()
                # 尝试解析类似于 "3个", "1.5斤", "500g", "2.0kg" 这种带有数量和单位的库存
                match = re.match(r"^([0-9\.]+)\s*([a-zA-Z\u4e00-\u9fa5]+)$", qty_str)
                if not match:
                    # 如果不是数字+单位的常规数量描述（例如“适量”、“少许”），直接移除
                    inventory.pop(name)
                    continue
                
                try:
                    val = float(match.group(1))
                    unit = match.group(2)
                except ValueError:
                    inventory.pop(name)
                    continue

                # 常规计件单位（按个/根/包等），进行部分扣减（每次扣减1个）
                count_units = {"个", "只", "支", "根", "条", "瓣", "块", "片", "包", "袋", "盒", "罐"}

                if unit in count_units:
                    new_val = val - 1.0
                    if new_val <= 0.001:
                        inventory.pop(name)
                    else:
                        if new_val.is_integer():
                            inventory[name] = f"{int(new_val)}{unit}"
                        else:
                            inventory[name] = f"{round(new_val, 1)}{unit}"
                else:
                    # 重量、容积等散装单位（如 g、克、斤、kg 等）默认一次性全部消耗，直接从冰箱移除
                    inventory.pop(name)
        
        try:
            with open(self.fridge_path, "w", encoding="utf-8") as f:
                json.dump(inventory, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Inventory] 扣减库存失败: {str(e)}")
        return inventory

    def parse_text_to_ingredients(self, user_text: str) -> Dict[str, str]:
        """
        使用 LLM 解析用户的口语输入，并返回结构化的食材及数量。
        例：“我刚买了两斤排骨、3个西红柿和一盒牛奶” -> {"排骨": "2斤", "西红柿": "3个", "牛奶": "1盒"}
        """
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model_name = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

        if not api_key:
            raise ValueError("未配置大模型 API_KEY，无法使用智能文本解析，请在侧边栏手动录入食材。")

        # 初始化大模型
        llm = ChatOpenAI(
            openai_api_key=api_key,
            openai_api_base=base_url,
            model_name=model_name,
            temperature=0.0
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", "你是一个精密的食材提取助手。请从用户的文字描述中，提取出所有买到的食材和对应数量，并以纯 JSON 格式输出。\n"
                       "不要输出任何解释文字，不要包裹在 ```json 代码块中，仅输出合法的 JSON 字典。\n"
                       "如果用户没有描述任何食材，输出空 JSON {{}}。\n"
                       "示例输入：“我买了2个苹果和半斤排骨”\n"
                       "示例输出：{{\"苹果\": \"2个\", \"排骨\": \"半斤\"}}"),
            ("user", "{text}")
        ])

        parser = JsonOutputParser()
        chain = prompt | llm | parser

        try:
            result = chain.invoke({"text": user_text})
            return result
        except Exception as e:
            print(f"[Inventory] LLM 解析文本失败: {str(e)}")
            # 简单降级：使用规则提取（若 LLM 失败）
            return {}

# 本地调试代码
if __name__ == "__main__":
    tool = InventoryTool(fridge_path="data/fridge_test.json")
    print("当前库存:", tool.get_inventory())
    tool.update_inventory({"鸡蛋": "10个", "西红柿": "5个"})
    print("更新后库存:", tool.get_inventory())
    tool.deduct_ingredients({"西红柿": "2个"})
    print("扣减后库存:", tool.get_inventory())
    
    # 测试大模型提取 (需要配置 .env 才有效)
    try:
        raw_text = "今天买了一斤猪肉和5个土豆"
        extracted = tool.parse_text_to_ingredients(raw_text)
        print("提取结果:", extracted)
        tool.update_inventory(extracted)
        print("更新后库存:", tool.get_inventory())
    except Exception as e:
        print("大模型解析测试跳过:", str(e))
