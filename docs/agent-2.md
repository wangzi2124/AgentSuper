---

## 13. 知识库设计补充

> 本章是对 §4.1 RAG 管线的生产级补强。原方案只定义了"向量检索 + 阈值过滤"，
> 缺三个环节：分块策略、稀疏检索路、检索质量门禁。本章在不引入新组件的前提下补齐。

### 13.1 三个缺口与对应调整

| 缺口 | 后果 | 调整 |
| --- | --- | --- |
| 分块策略未定义 | chunk 边界靠默认值，检索对象质量不可控 | 结构感知分块 + 父子文档（§13.2） |
| 仅向量检索 | 专有名词、编号、故障码等精确查询漏召严重 | BM25 稀疏路 + RRF 融合（§13.3） |
| 检索后无质量校验 | 阈值只能过滤低分，不能过滤"高分但无关" | LangGraph 加 grade 节点（§13.4） |

**核心原则**：分块决定检索上限，双路检索保证召回下限，grade 门禁阻止噪声进入下游。
三者都在现有 LangGraph 图内完成，新增 LLM 调用只有 grade 和 rewrite 两处，用 `tier-simple` 兜底。

---

### 13.2 分块策略：结构感知 + 父子文档

#### 13.2.1 按文档类型选择分块方式

| 文档类型 | 分块策略 | chunk 参数 | 理由 |
| --- | --- | --- | --- |
| Markdown / 技术文档 | 按标题层级切分（H2→H3） | 父块 800~1200 字符，子块 300~400 字符 | 保留小节完整性，标题参与检索 |
| PDF / Word | 按段落 + 重叠窗口 | 512 token，overlap 10~15% | 通用起点 |
| FAQ / Q&A | 一问一答为一个 chunk，不拆分 | 以语义完整为准 | 防止问题与答案被腰斩 |
| 表格 | 转 Markdown 表格或键值对，单独成 chunk | 按行数或字符数限制 | 企业文档中表格常含关键信息 |

**metadata 强制字段**（每个 chunk 必须带）：

```text
parent_id      父文档或父小节 ID（父子召回用）
source_file    源文件名
section_title  所属小节标题（拼入检索文本）
department     部门/租户（权限过滤用）


```

13.3 检索层：向量 + BM25 双路 + RRF 融合
单一向量检索对精确查询（"TS-999 故障码"、"合同第 3.2 条"）召回极差，必须双路互补。

13.3.1 混合检索器
python
# cognition/rag.py
class HybridRetriever:
    def __init__(self):
        self.vector_store = chromadb_client...   # 原有
        self.bm25_index = self._build_bm25()      # 新增

    def retrieve(self, query, top_k=10, score_threshold=0.35):
        vec_hits  = self._vector_search(query, top_k=top_k)
        bm25_hits = self._bm25_search(query, top_k=top_k)
        fused = self._rrf_fuse(vec_hits, bm25_hits, k=60)   # RRF 融合
        return [
            self._expand_to_parent(h)
            for h in fused
            if h["score"] >= score_threshold
        ][:top_k]
RRF 公式：score(d) = Σ 1/(k + rank_i(d))，k 取 60。
优势：不需调两路权重，排名靠前的结果自然获得更高融合分。

13.3.2 BM25 索引维护
用 rank_bm25 在内存构建，随文档更新重建。万级文档重建在秒级，可在 index_documents() 中同步完成。

python
from rank_bm25 import BM25Okapi
import jieba

def _build_bm25(self):
    all_chunks = self.collection.get()["documents"]
    tokenized = [list(jieba.cut(c)) for c in all_chunks]
    return BM25Okapi(tokenized)
13.4 LangGraph 图调整：检索后加 grade 节点
原图 rag → execute_subagent 直连，缺"检索结果是否真的相关"判断。
新增 grade_docs 和 rewrite 两节点，不相关则改写重试（最多 2 次）。

13.4.1 图结构
python
# graph/main_graph.py
builder.add_node("rag",        rag_node)
builder.add_node("grade_docs", grade_documents_node)   # 新增
builder.add_node("rewrite",    rewrite_query_node)      # 新增

builder.add_edge("rag", "grade_docs")
builder.add_conditional_edges(
    "grade_docs",
    route_after_grade,
    {"generate": "execute_subagent", "rewrite": "rewrite"},
)
builder.add_edge("rewrite", "rag")
13.4.2 grade 与 rewrite 节点（均用 tier-simple）
python
# graph/nodes/rag.py
GRADE_PROMPT = """判断以下文档是否与用户问题相关。只输出 yes 或 no。

文档：{context}
问题：{question}"""

def grade_documents_node(state):
    model = get_compress_model()          # tier-simple，复用 §1.3 的 compress-model
    resp = model.invoke(GRADE_PROMPT.format(
        context=state["rag_context"][:2000],
        question=state["messages"][-1].content,
    ))
    state["rag_relevant"] = "yes" in resp.content.lower()
    return state

def route_after_grade(state):
    if state["rag_relevant"]:
        return "generate"
    if state.get("rewrite_count", 0) >= 2:
        return "generate"                 # 改写 2 次仍不相关，带空结果继续
    state["rewrite_count"] = state.get("rewrite_count", 0) + 1
    return "rewrite"
rewrite_query_node 直接复用 §7.5.1 的 prompts/nodes/rag_rewrite.md，改写结果写入
state["rewritten_query"]，rag_node 优先使用它而非原始消息。

成本：grade 约 500 token in + 5 token out，改写约 200 token。
即使触发 2 轮重试，额外成本 < ¥0.01。远低于无关文档进入子 Agent 后的无效推理开销。

13.4.3 新增 State 字段
字段	写入节点	读取节点	生命周期
rag_relevant	grade_docs	route_after_grade	单轮
rewrite_count	route_after_grade	route_after_grade	单轮
rewritten_query	rewrite	rag	单轮
13.5 与 LiteLLM 的协同
embedding 统一走 Proxy：litellm.embedding(model="embedding", ...)，不引入本地
sentence-transformers，否则向量空间不一致会导致检索质量下降。

检索过程不产生 LLM 调用：向量检索和 BM25 都是确定性计算。新增 LLM 调用仅
grade 和 rewrite 两处，均被 rewrite_count 上限和 tier-simple 约束。

成本归因：LangFuse 打标签 stage=rag_grade / stage=rag_rewrite，便于观测。

python
def get_embedding(texts: list[str]) -> list[list[float]]:
    resp = litellm.embedding(model="embedding", input=texts)
    return [d["embedding"] for d in resp.data]
13.6 调整后的知识库数据流
text
用户查询
   │
   ▼
[rag_node]
   ├─ 查询改写（首次用原查询，重试时用 rewritten_query）
   ├─ 向量检索（Chroma，embedding 走 LiteLLM）
   ├─ BM25 检索（内存索引，jieba 分词）
   ├─ RRF 融合
   ├─ 阈值过滤（score >= 0.35）
   └─ 父块展开（子块命中 → 返回父块内容）
   │
   ▼
[grade_docs]  ← tier-simple，只输出 yes/no
   │
   ├─ yes → [execute_subagent]（原流程不变）
   │
   └─ no → [rewrite]（tier-simple，最多 2 次）→ 回 [rag_node]
13.7 落地优先级
顺序	改动	工作量	收益
1	分块改结构感知 + metadata 补齐	1~2 天	检索对象质量从"默认切分"变为"语义完整"
2	加 BM25 路 + RRF 融合	0.5 天	精确查询漏召率显著下降
3	LangGraph 加 grade + rewrite 节点	1 天	无关文档不再污染子 Agent 上下文
4	父子文档召回	0.5 天	子块精准命中，父块提供完整上下文
不建议：不要上知识图谱（GraphRAG）。企业 Agent 平台的核心需求不是实体关系推理，
GraphRAG 维护成本远高于收益。混合检索 + grade 门禁已能把知识库拉到生产可用水平。

13.8 知识库设计决策记录（追加至附录 A）
编号	决策	备选方案	选择理由	代价
D11	结构感知分块 + 父子文档	固定长度滑窗	检索对象语义完整，子块精准父块完整	分块器需按格式维护
D12	BM25 + 向量双路 + RRF	仅向量 / 加权融合	精确查询召回，RRF 免调权重	内存需维护 BM25 索引
D13	LangGraph grade 节点门禁	仅相似度阈值	阈值过滤不了"高分但无关"	每次检索多一次 simple tier 调用
D14	grade/rewrite 用 tier-simple	用 complex tier	二分类与改写是轻任务，simple 足够	极端 case 改写质量略低
D15	不上 GraphRAG	知识图谱增强	企业 Agent 非实体推理场景，维护成本高	跨文档关系推理能力缺失
13.9 陷阱清单追加（追加至附录 B）
陷阱	后果	规避
分块不保留 metadata	无法父块召回、无法权限过滤	强制 parent_id / department 字段
只做向量检索	编号、故障码、条款号漏召	BM25 双路 + RRF
grade 用 complex tier	每次检索成本翻 10 倍	固定 compress-model（tier-simple）
改写无次数上限	检索死循环	rewrite_count >= 2 强制放行
embedding 混用来源	向量空间不一致，检索质量下降	统一走 LiteLLM embedding 入口