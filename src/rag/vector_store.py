import os
import json
import jieba
from typing import List, Dict, Any, Optional
from rank_bm25 import BM25Okapi
import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv

load_dotenv()

# 基础辅料列表，过滤冰箱食材匹配时可以忽略这些调味配料
COMMON_SEASONINGS = {"盐", "白糖", "生姜", "小葱", "大葱", "大蒜", "料酒", "生抽", "老抽", "淀粉", "食用油", "冰糖", "八角", "花椒", "香油", "白醋", "鸡精", "小米辣", "干辣椒"}

class RecipeRetriever:
    """食谱混合检索器 (BM25 + Chroma Vector DB)"""
    def __init__(self, recipes_path: Optional[str] = None, persist_directory: str = "data/chroma_db"):
        if recipes_path is None:
            # 优先读取环境变量，再尝试自适应加载 data/recipe，最后补上 JSON 路径作为兜底
            recipes_path = os.getenv("RECIPES_PATH")
            if not recipes_path:
                if os.path.exists("data/recipe"):
                    recipes_path = "data/recipe"
                else:
                    recipes_path = "data/recipes.json"

        self.recipes_path = recipes_path
        self.persist_directory = persist_directory
        self.recipes: List[Dict[str, Any]] = []
        self.bm25: Optional[BM25Okapi] = None
        self.chroma_client: Optional[chromadb.PersistentClient] = None
        self.collection: Optional[chromadb.Collection] = None
        self.use_vector: bool = False

        # 1. 加载食谱数据
        self._load_recipes()

        # 2. 初始化 BM25
        self._init_bm25()

        # 3. 初始化 Chroma 向量数据库
        self._init_chroma()

    def _load_recipes(self):
        """加载本地的食谱数据 (支持单文件测试模式与多源目录合并模式)"""
        self.recipes = []
        
        # 1. 单文件测试模式：如果 recipes_path 指向一个具体存在的 JSON 文件，则仅加载该文件数据
        if self.recipes_path and os.path.exists(self.recipes_path) and not os.path.isdir(self.recipes_path):
            try:
                with open(self.recipes_path, "r", encoding="utf-8") as f:
                    self.recipes = json.load(f)
                    print(f"[RAG] 成功单源加载食谱数据: {self.recipes_path}，共 {len(self.recipes)} 道。")
            except Exception as e:
                print(f"[RAG] 加载单源 {self.recipes_path} 失败: {str(e)}")
            return

        # 2. 多源目录合并模式：如果是目录，则优先加载 Markdown 目录，再同步合并 recipes.json
        md_dir = self.recipes_path if (self.recipes_path and os.path.isdir(self.recipes_path)) else "data/recipe"
        if os.path.exists(md_dir) and os.path.isdir(md_dir):
            md_recipes = self._load_from_markdown_dir(md_dir)
            self.recipes.extend(md_recipes)
            print(f"[RAG] 成功解析并从 {md_dir} 加载了 {len(md_recipes)} 道 Markdown 食谱。")
            
        json_file = "data/recipes.json"
        if os.path.exists(json_file):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    json_recipes = json.load(f)
                    self.recipes.extend(json_recipes)
                    print(f"[RAG] 成功合并加载了 {len(json_recipes)} 道 JSON 大库食谱。")
            except Exception as e:
                print(f"[RAG] 加载 recipes.json 失败: {str(e)}")
                
        # 3. 兜底处理
        if not self.recipes:
            os.makedirs(os.path.dirname(json_file), exist_ok=True)
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
            print(f"[RAG] 未找到任何食谱数据，已自动初始化空 JSON 库: {json_file}")

    def _load_from_markdown_dir(self, directory: str) -> List[Dict[str, Any]]:
        import glob
        pattern = os.path.join(directory, "**", "*.md")
        files = glob.glob(pattern, recursive=True)
        recipes = []
        for filepath in files:
            normalized_path = filepath.replace("\\", "/")
            if "template" in normalized_path or "README.md" in normalized_path or "CONTRIBUTING.md" in normalized_path:
                continue
            try:
                recipe = self._parse_markdown_recipe(filepath)
                if recipe and recipe["ingredients"] and recipe["steps"]:
                    recipes.append(recipe)
            except Exception as e:
                print(f"[RAG] 解析食谱文件失败 {filepath}: {str(e)}")
        return recipes

    def _parse_markdown_recipe(self, filepath: str) -> Optional[Dict[str, Any]]:
        import re
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 1. 提取菜名
        name = os.path.basename(filepath).replace(".md", "")
        match_title = re.search(r'^#\s*(.*?)$', content, re.MULTILINE)
        if match_title:
            title = match_title.group(1).strip()
            title = re.sub(r'(表达|的做法|的制作方法|的制作步骤|制作方法|的制作|指南|做法指南|的做法指南)$', '', title)
            if title:
                name = title

        # 2. 提取时长
        cooking_time = 20
        time_matches = re.findall(r'(?:时间|耗时|需要|大约)\s*(\d+)\s*分钟', content)
        if time_matches:
            cooking_time = int(time_matches[0])
        else:
            time_matches_any = re.findall(r'(\d+)\s*分钟', content)
            if time_matches_any:
                cooking_time = int(time_matches_any[0])
                
        # 3. 提取食材
        ingredients = []
        section_pattern = re.compile(
            r'##\s*(?:必备原料|原料|原材料|必备原料和工具|准备|材料).*?\n(.*?)(?=\n##(?:[^#]|$))', 
            re.DOTALL | re.IGNORECASE
        )
        match_sec = section_pattern.search(content)
        if match_sec:
            sec_content = match_sec.group(1)
            for line in sec_content.split('\n'):
                line = line.strip()
                m = re.match(r'^[-*+]\s*(.*?)$', line)
                if m:
                    ing = m.group(1).strip()
                    if ing in {"碗", "锅", "勺", "勺子", "刀", "烤箱", "微波炉", "空气炸锅", "高压锅", "平底锅", "炒锅", "砂锅", "蒸锅", "砧板", "量杯", "厨房秤"}:
                        continue
                    parts = ing.split()
                    if parts:
                        clean_ing = parts[0]
                        clean_ing = re.sub(r'[\(\)（）\d\s克gmlkg升千克毫升支个根块片包粒瓶适量少许].*$', '', clean_ing)
                        clean_ing = clean_ing.strip()
                        if clean_ing and len(clean_ing) >= 1:
                            ingredients.append(clean_ing)
        
        # 兜底食材匹配：从 "## 计算" 中提取
        if not ingredients:
            match_calc = re.search(
                r'##\s*(?:计算).*?\n(.*?)(?=\n##(?:[^#]|$))', 
                content, 
                re.DOTALL
            )
            if match_calc:
                sec_content = match_calc.group(1)
                for line in sec_content.split('\n'):
                    line = line.strip()
                    m = re.match(r'^[-*+]\s*(.*?)$', line)
                    if m:
                        ing = m.group(1).strip()
                        parts = ing.split()
                        if parts:
                            clean_ing = parts[0]
                            clean_ing = re.sub(r'[\(\)（）\d\s克gmlkg升千克毫升支个根块片包粒瓶适量少许].*$', '', clean_ing)
                            clean_ing = clean_ing.strip()
                            if clean_ing and len(clean_ing) >= 1:
                                ingredients.append(clean_ing)

        # 4. 提取步骤
        steps = []
        step_pattern = re.compile(
            r'##\s*(?:操作|步骤|制作步骤|制作方法|做法|流程).*?\n(.*?)(?=\n##(?:[^#]|$))', 
            re.DOTALL | re.IGNORECASE
        )
        match_step = step_pattern.search(content)
        if match_step:
            sec_content = match_step.group(1)
            for line in sec_content.split('\n'):
                line = line.strip()
                m = re.match(r'^(?:[-*+]|\d+\.)\s*(.*?)$', line)
                if m:
                    step = m.group(1).strip()
                    if step:
                        steps.append(step)

        # 5. 提取标签与分类
        tags = ["家常菜"]
        path_parts = filepath.replace("\\", "/").split("/")
        category = ""
        for idx, part in enumerate(path_parts):
            if part == "dishes" and idx + 1 < len(path_parts):
                category = path_parts[idx + 1]
                break
        
        category_map = {
            "breakfast": "早餐",
            "meat_dish": "荤菜",
            "vegetable_dish": "素菜",
            "aquatic": "海鲜",
            "soup": "汤",
            "staple": "主食",
            "dessert": "甜点",
            "drink": "饮品",
            "condiment": "调味品",
            "semi-finished": "半成品"
        }
        if category in category_map:
            tags.append(category_map[category])
            
        difficulty_match = re.search(r'难度：(★+)', content)
        if difficulty_match:
            tags.append(f"难度 {len(difficulty_match.group(1))} 星")

        # 记录元数据相对路径以备追溯
        source_rel = os.path.relpath(filepath, self.recipes_path).replace("\\", "/") if os.path.isdir(self.recipes_path) else filepath

        # 使用基于相对路径的唯一 ID，防止不同子目录下同名菜谱引发 Chroma ID 冲突
        safe_path_id = source_rel.replace("/", "_").replace("\\", "_").replace(".md", "")
        recipe_id = f"recipe_{safe_path_id}"

        return {
            "id": recipe_id,
            "name": name,
            "ingredients": list(set(ingredients)),
            "steps": steps,
            "tags": tags,
            "cooking_time_minutes": cooking_time,
            "calories": 200,
            "season": "四季",
            "region": "通用",
            "source_file": source_rel
        }

    def _init_bm25(self):
        """初始化 BM25 检索器"""
        if not self.recipes:
            print("[RAG] 食谱数据库为空，跳过 BM25 检索器初始化。")
            self.bm25 = None
            return

        corpus = []
        for recipe in self.recipes:
            # 将名称、食材、标签组合成文本，作为分词内容
            text = f"{recipe['name']} {' '.join(recipe['ingredients'])} {' '.join(recipe['tags'])}"
            words = list(jieba.cut(text))
            corpus.append(words)
        self.bm25 = BM25Okapi(corpus)
        print("[RAG] BM25 检索器初始化完成。")

    def _init_chroma(self):
        """初始化 Chroma 向量数据库 (若未配置 API Key 则优雅降级)"""
        # 优先读取专门的 EMBEDDING 环境变量，若未配置则降级读取大模型的 OPENAI 配置
        api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("EMBEDDING_BASE_URL") or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

        if not api_key:
            print("[RAG] [警告] 未检测到 OPENAI_API_KEY，向量检索将关闭，系统将降级为纯 BM25 关键词检索。")
            self.use_vector = False
            return

        try:
            self.chroma_client = chromadb.PersistentClient(path=self.persist_directory)
            # 使用 OpenAI 兼容的 Embedding 函数
            openai_ef = embedding_functions.OpenAIEmbeddingFunction(
                api_key=api_key,
                api_base=base_url,
                model_name=embedding_model
            )
            self.collection = self.chroma_client.get_or_create_collection(
                name="recipes_collection",
                embedding_function=openai_ef
            )

            # 增量导入：仅导入数据库中不存在的食谱向量
            existing_ids = set()
            try:
                # include=[] 能够大幅缩短查询时间和减少网络消耗
                existing_res = self.collection.get(include=[])
                if existing_res and "ids" in existing_res:
                    existing_ids = set(existing_res["ids"])
            except Exception as e:
                print(f"[RAG] 获取已有向量 ID 失败: {str(e)}")

            to_insert_recipes = [r for r in self.recipes if r["id"] not in existing_ids]

            if to_insert_recipes:
                print(f"[RAG] 检测到有 {len(to_insert_recipes)} 道新食谱未进行向量化，开始增量导入向量数据库...")
                ids = [recipe["id"] for recipe in to_insert_recipes]
                documents = [
                    f"菜名: {recipe['name']}。主要食材: {', '.join(recipe['ingredients'])}。标签: {', '.join(recipe['tags'])}。"
                    for recipe in to_insert_recipes
                ]
                metadatas = [
                    {
                        "id": recipe["id"],
                        "name": recipe["name"],
                        "season": recipe["season"],
                        "region": recipe["region"],
                        "calories": recipe["calories"],
                        "cooking_time": recipe["cooking_time_minutes"],
                        "source_file": recipe.get("source_file", "")
                    }
                    for recipe in to_insert_recipes
                ]
                
                batch_size = 32
                total_inserted = 0
                for i in range(0, len(ids), batch_size):
                    batch_ids = ids[i : i + batch_size]
                    batch_docs = documents[i : i + batch_size]
                    batch_metas = metadatas[i : i + batch_size]
                    self.collection.add(
                        ids=batch_ids,
                        documents=batch_docs,
                        metadatas=batch_metas
                    )
                    total_inserted += len(batch_ids)
                    print(f"[RAG] 已成功增量导入 {total_inserted}/{len(ids)} 条食谱向量。")
                print(f"[RAG] 向量数据库增量同步完毕。")
            else:
                print(f"[RAG] 向量数据库连接成功，所有 {len(self.recipes)} 道食谱已处于向量化同步状态。")
            self.use_vector = True
        except Exception as e:
            print(f"[RAG] 向量数据库初始化失败 ({str(e)})，将降级为纯 BM25 检索。")
            self.use_vector = False

    def _filter_recipe(
        self,
        recipe: Dict[str, Any],
        ingredients_must_have: Optional[List[str]] = None,
        ingredients_avoid: Optional[List[str]] = None
    ) -> bool:
        """
        核心过滤算法：
        1. 避开忌口/过敏源 (ingredients_avoid): 必须 100% 绝对过滤掉。
        2. 冰箱库存匹配 (ingredients_must_have):
           - 如果是做饭模式下，必须包含用户的部分核心食材。
           - 允许缺少部分非核心食材（如基础调味配料）。
           - 允许缺少最多 N 个配菜（可在界面提示采购），这里设定主要食材匹配度比例。
        """
        recipe_ingredients = set(recipe["ingredients"])

        # 1. 过敏源过滤 (硬过滤)
        if ingredients_avoid:
            # 转换成集合
            avoid_set = set(ingredients_avoid)
            # 只要食谱里的食材与过敏源有交集，即剔除
            if recipe_ingredients & avoid_set:
                return False

        # 2. 冰箱库存过滤 (做饭模式)
        if ingredients_must_have:
            must_have_set = set(ingredients_must_have)
            
            # 过滤掉食谱中的常见基础调料，提取出核心食材
            core_recipe_ingredients = recipe_ingredients - COMMON_SEASONINGS
            
            # 计算冰箱里的食材在核心食材中的重合部分
            matched_core = core_recipe_ingredients & must_have_set
            
            # 如果食谱里的所有核心食材，用户一样都没有，那显然无法做这道菜
            if not matched_core:
                return False
                
            # 规则：匹配上的核心食材占比需要达到一定程度（例如：至少匹配上 50% 的核心食材），或者只缺 1 样主菜
            # 以免出现用户只有“鸡蛋”，却推荐“番茄牛腩面（需要牛肉、面条、西红柿、小葱）”这种相差太大的推荐
            missing_core = core_recipe_ingredients - must_have_set
            if len(missing_core) > 1 and len(matched_core) / len(core_recipe_ingredients) < 0.5:
                return False

        return True

    def search(
        self,
        query: str,
        ingredients_must_have: Optional[List[str]] = None,
        ingredients_avoid: Optional[List[str]] = None,
        season_filters: Optional[List[str]] = None,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        混合检索核心函数 (BM25 + Vector DB + Rule Filtering + Season Boosting)
        """
        # 1. 规则过滤筛选出可选的候选食谱列表
        candidates = []
        candidate_indices = []
        for i, recipe in enumerate(self.recipes):
            if self._filter_recipe(recipe, ingredients_must_have, ingredients_avoid):
                candidates.append(recipe)
                candidate_indices.append(i)

        if not candidates:
            return []

        # 2. 计算 BM25 得分
        query_words = list(jieba.cut(query))
        all_bm25_scores = self.bm25.get_scores(query_words)
        # 提取候选食谱的 BM25 分数
        candidate_bm25_scores = [all_bm25_scores[idx] for idx in candidate_indices]

        # 3. 计算 向量 得分（若启用）
        candidate_vector_scores = [0.0] * len(candidates)
        if self.use_vector and self.collection:
            try:
                # 查询 Chroma
                vector_results = self.collection.query(
                    query_texts=[query],
                    n_results=len(self.recipes) # 查出所有做相似度匹配
                )
                
                # 建立 id -> 相似度距离 的映射
                # Chroma 的 distances 越小越相似，此处我们将距离转化为得分：score = 1 / (1 + distance)
                id_to_score = {}
                if vector_results and "ids" in vector_results and len(vector_results["ids"]) > 0:
                    result_ids = vector_results["ids"][0]
                    result_distances = vector_results["distances"][0]
                    for rid, rdist in zip(result_ids, result_distances):
                        # 转换成正向得分，范围大致在 [0, 1]
                        id_to_score[rid] = 1.0 / (1.0 + float(rdist))
                
                for idx, r in enumerate(candidates):
                    candidate_vector_scores[idx] = id_to_score.get(r["id"], 0.0)
            except Exception as e:
                print(f"[RAG] 向量检索查询出错 ({str(e)})，仅依赖 BM25。")

        # 4. 分数归一化与融合 (Min-Max 归一化)
        def normalize_list(lst: List[float]) -> List[float]:
            min_val, max_val = min(lst), max(lst)
            if max_val == min_val:
                return [1.0] * len(lst)
            return [(x - min_val) / (max_val - min_val) for x in lst]

        norm_bm25 = normalize_list(candidate_bm25_scores)
        norm_vector = normalize_list(candidate_vector_scores) if self.use_vector else [0.0] * len(candidates)

        # 5. 计算混合得分并引入时令 Boost 和名字精准匹配 Boost
        scored_candidates = []
        for idx, recipe in enumerate(candidates):
            # 融合权重：40% 关键词 + 60% 向量语义
            blend_score = 0.4 * norm_bm25[idx] + 0.6 * norm_vector[idx] if self.use_vector else norm_bm25[idx]
            
            # 时令偏好加权 (Season Boosting)
            boost = 0.0
            if season_filters:
                if recipe["season"] in season_filters:
                    boost += 0.25
                elif recipe["season"] == "四季":
                    boost += 0.05
                    
            # 名字精准匹配加权 (Title Matching Boosting)
            title_boost = 0.0
            if query:
                # 去除动作词和语气词，提取核心搜索项
                clean_q = query.replace("就做", "").replace("确认做", "").replace("来做", "").replace("做", "").replace("怎么做", "").replace("做法", "").replace("吧", "").replace("啦", "").strip()
                if clean_q and (clean_q in recipe["name"] or recipe["name"] in clean_q):
                    title_boost += 2.0  # 名字匹配度极高时给予大加成，防止极小文档集下 BM25 分数失效

            final_score = blend_score + boost + title_boost
            scored_candidates.append((recipe, final_score))

        # 6. 按最终得分排序并进行去重，确保同道菜（父文档）只出现一次，并返回前 top_k 个完整食谱数据
        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        results = []
        seen_ids = set()
        for r, score in scored_candidates:
            if r["id"] in seen_ids:
                continue
            seen_ids.add(r["id"])
            
            r_copy = r.copy()
            # 在返回数据中动态计算并注入“缺失食材”的标记，便于前端展示
            if ingredients_must_have:
                core_ingredients = set(r_copy["ingredients"]) - COMMON_SEASONINGS
                missing = list(core_ingredients - set(ingredients_must_have))
                r_copy["missing_ingredients"] = missing
            else:
                r_copy["missing_ingredients"] = []
            
            r_copy["hybrid_score"] = round(float(score), 4)
            results.append(r_copy)
            if len(results) >= top_k:
                break

        return results

# 本地调试代码
if __name__ == "__main__":
    load_dotenv()
    # 针对 data/recipe 文件夹进行批量 Markdown 提取与检索测试
    retriever = RecipeRetriever(recipes_path="data/recipe", persist_directory="data/chroma_db_test")
    print(f"Chroma 向量数据库载入完毕，共导入了 {len(retriever.recipes)} 道食谱。")
    
    print("\n--- 测试场景1：检索 '茶叶蛋' ---")
    results = retriever.search(
        query="茶叶蛋",
        ingredients_must_have=["鸡蛋"],
        season_filters=["四季"]
    )
    for res in results:
        print(f"推荐菜名: {res['name']}")
        print(f"  - 溯源文件: {res.get('source_file')}")
        print(f"  - 匹配得分: {res['hybrid_score']}")
        print(f"  - 完整步骤: {res['steps']}")
