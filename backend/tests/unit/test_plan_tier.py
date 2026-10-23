"""L1 单元测试：计划档位判定（agent-1.md §2.2 / §2.4）。"""

from langchain_core.messages import HumanMessage

from graph.nodes.plan import classify_plan_tier, make_plan_node


def test_short_request_is_single():
    assert classify_plan_tier("列出目录") == "single"
    assert classify_plan_tier("读取 a.txt") == "single"
    assert classify_plan_tier("") == "single"


def test_short_question_with_parallel_word_is_single():
    # 「分别」在短问句里只是并列疑问，不是并行任务信号。
    assert classify_plan_tier("项目代号和端口分别是什么？只回答这两个值。") == "single"
    assert classify_plan_tier("A 和 B 分别是什么？") == "single"


def test_serial_signals():
    assert classify_plan_tier("先读取配置，然后修改端口再重启服务") == "serial"
    assert classify_plan_tier("根据以上分析，接着生成报告并保存到文件里") == "serial"


def test_parallel_signals():
    assert classify_plan_tier("分别调研 RAG 与向量数据库，同时给出对比") == "parallel"
    assert classify_plan_tier("各自独立完成这三个模块的设计") == "parallel"


def test_serial_beats_parallel_when_both_present():
    # "先 A 然后分别 B/C" 有依赖 → 串行。
    assert classify_plan_tier("先做 A，然后分别处理 B 和 C") == "serial"


def test_long_descriptive_is_parallel_capable():
    long = "帮我系统地梳理一下整个系统的架构设计，包括各个模块之间的调用关系、数据流向以及" \
           "可能的性能瓶颈和优化空间，并给出改进建议" * 2
    assert classify_plan_tier(long) == "parallel"


def test_long_with_question_is_serial():
    long_q = "能不能详细解释一下这个系统为什么在并发场景下会出现数据不一致的问题以及" \
             "应该如何排查和修复" * 2 + "？"
    assert classify_plan_tier(long_q) == "serial"


async def test_plan_node_single_skips_model():
    class _Boom:
        tier = "x"

        async def ainvoke(self, *a, **k):
            raise AssertionError("single tier must not call model")

    node = make_plan_node(_Boom())
    out = await node({"messages": [HumanMessage(content="列出目录")]})
    assert out["plan_tier"] == "single"
    assert out["task_plan"][0]["status"] == "pending"
