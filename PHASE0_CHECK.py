#!/usr/bin/env python3
"""
Phase 0 验收脚本
检查核心模型、配置、数据库是否就绪
"""
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

def check_imports():
    """检查导入是否正常"""
    print("✓ 检查导入...")
    try:
        from core.models import RawEvent, MemoryEvent, MemoryCandidate, MemoryRecord, RetrievalResult, ForgetCommand
        from core.constants import EventType, MemoryType, MemoryStatus, Scene
        from core.config import MEMORY_STORE_PATH, DEMO_DATA_PATH
        from api.schemas import PostEventsRequest, PostRetrieveRequest, PostForgetRequest, APIResponse
        from memory.store import init_db, save_event, save_memory, list_active_memories, mark_memory_deleted
        print("  ✓ 所有导入成功")
        return True
    except ImportError as e:
        print(f"  ✗ 导入失败: {e}")
        return False


def check_models():
    """检查数据模型"""
    print("✓ 检查数据模型...")
    from core.models import MemoryCandidate, MemoryRecord
    from core.constants import MemoryType, Scene
    from datetime import datetime

    try:
        # 测试 MemoryCandidate
        candidate = MemoryCandidate(
            user_id="u001",
            memory_type=MemoryType.PREFERENCE,
            key="test_key",
            content="Test content",
        )
        assert candidate.to_dict()["memory_type"] == "preference"

        # 测试 MemoryRecord
        record = MemoryRecord(
            memory_id="mem_001",
            user_id="u001",
            memory_type=MemoryType.KNOWLEDGE,
            key="test_key",
            content="Test knowledge",
        )
        assert record.to_dict()["status"] == "active"

        print("  ✓ 数据模型正常")
        return True
    except Exception as e:
        print(f"  ✗ 数据模型失败: {e}")
        return False


def check_database():
    """检查数据库初始化"""
    print("✓ 检查数据库...")
    from memory.store import init_db, save_memory, list_active_memories
    from core.models import MemoryCandidate
    from core.constants import MemoryType
    from pathlib import Path
    import tempfile

    try:
        # 使用临时数据库测试
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            init_db(str(db_path))

            # 测试保存记忆
            candidate = MemoryCandidate(
                user_id="u001",
                memory_type=MemoryType.PREFERENCE,
                key="test",
                content="Test content",
            )
            record = save_memory(str(db_path), candidate)
            assert record.memory_id is not None
            assert record.status.value == "active"

            # 测试查询
            memories = list_active_memories(str(db_path), "u001")
            assert len(memories) == 1

        print("  ✓ 数据库操作正常")
        return True
    except Exception as e:
        print(f"  ✗ 数据库失败: {e}")
        return False


def check_demo_data():
    """检查演示数据"""
    print("✓ 检查演示数据...")
    from core.config import DEMO_DATA_PATH

    try:
        if not DEMO_DATA_PATH.exists():
            print(f"  ✗ 演示数据不存在: {DEMO_DATA_PATH}")
            return False

        with open(DEMO_DATA_PATH) as f:
            lines = f.readlines()
            if len(lines) < 6:
                print(f"  ✗ 演示数据不足（期望 6 条，实际 {len(lines)}）")
                return False

        print(f"  ✓ 演示数据正常（{len(lines)} 条）")
        return True
    except Exception as e:
        print(f"  ✗ 演示数据失败: {e}")
        return False


def main():
    """主验收流程"""
    print("\n" + "=" * 50)
    print("Phase 0 验收检查")
    print("=" * 50 + "\n")

    results = []
    results.append(("导入检查", check_imports()))
    if not results[-1][1]:
        print("\n✗ 导入失败，无法继续检查")
        return False

    results.append(("数据模型", check_models()))
    results.append(("数据库操作", check_database()))
    results.append(("演示数据", check_demo_data()))

    print("\n" + "=" * 50)
    print("验收结果")
    print("=" * 50)
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{name:20} {status}")

    all_passed = all(r[1] for r in results)
    print("\n" + ("✓ Phase 0 就绪，可以开始开发！" if all_passed else "✗ Phase 0 未就绪，请检查错误"))
    print()

    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
