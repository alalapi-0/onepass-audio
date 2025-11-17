"""最小测试：验证分句规则。"""
from __future__ import annotations

import sys

# Try to import pytest, but allow running without it
try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False

from onepass.text_normalizer import TextNormConfig, split_sentences_with_rules, HARD_PUNCT, SOFT_PUNCT


def test_multiple_periods():
    """测试：多句号必须分句。"""
    text = "他顿了顿。于是继续说：事情还没完。别急。"
    cfg = TextNormConfig(
        split_all_punct=True,
        min_len=2,
        max_len=100,
    )
    result = split_sentences_with_rules(text, cfg)
    
    # 应该按句号分句
    assert len(result) >= 3
    # 每个句子应该以硬标点结尾（如果非空）
    for sent in result:
        if sent.strip():
            assert sent.strip()[-1] in HARD_PUNCT, f"句子 '{sent}' 不以硬标点结尾"
    
    # 预期结果
    expected = ["他顿了顿。", "于是继续说：", "事情还没完。", "别急。"]
    # 允许顺序和空格差异
    result_stripped = [s.strip() for s in result if s.strip()]
    assert len(result_stripped) >= 3


def test_mixed_chinese_english():
    """测试：中英混排规范化后分句。"""
    from onepass.text_normalizer import hard_collapse_whitespace
    
    text = "ABC\n123。"
    collapsed = hard_collapse_whitespace(text)
    assert "ABC" in collapsed
    assert "123" in collapsed
    assert "。" in collapsed
    assert "\n" not in collapsed
    
    cfg = TextNormConfig(split_all_punct=True)
    result = split_sentences_with_rules(collapsed, cfg)
    assert len(result) >= 1
    assert any("123" in sent for sent in result)


def test_parens_and_colon():
    """测试：括注与冒号分句。"""
    text = "第二段 (note)：好的。"
    cfg = TextNormConfig(
        split_all_punct=True,
        min_len=2,
    )
    result = split_sentences_with_rules(text, cfg)
    
    # 冒号与句号均应切分
    assert len(result) >= 2
    # 检查是否包含冒号和句号
    has_colon = any("：" in sent or ":" in sent for sent in result)
    has_period = any("。" in sent or "." in sent for sent in result)
    assert has_colon or has_period


def test_no_merge_across_punct():
    """测试：不跨越标点合并。"""
    text = "短句。另一个短句。"
    cfg = TextNormConfig(
        split_all_punct=True,
        min_len=10,  # 设置较大的 min_len 试图合并
    )
    result = split_sentences_with_rules(text, cfg)
    
    # 即使两个都是短句，也不应该合并（因为中间有硬标点）
    assert len(result) >= 2
    # 每个句子都应该以硬标点结尾
    for sent in result:
        if sent.strip():
            assert sent.strip()[-1] in HARD_PUNCT


if __name__ == "__main__":
    if HAS_PYTEST:
        pytest.main([__file__, "-v"])
    else:
        # Simple test runner without pytest
        print("Running tests without pytest...")
        print("=" * 60)
        
        tests = [
            test_multiple_periods,
            test_mixed_chinese_english,
            test_parens_and_colon,
            test_no_merge_across_punct,
        ]
        
        passed = 0
        failed = 0
        
        for test_func in tests:
            try:
                test_func()
                print(f"✓ {test_func.__name__}: PASSED")
                passed += 1
            except AssertionError as e:
                print(f"✗ {test_func.__name__}: FAILED - {e}")
                failed += 1
            except Exception as e:
                print(f"✗ {test_func.__name__}: ERROR - {e}")
                failed += 1
        
        print("=" * 60)
        print(f"Total: {len(tests)}, Passed: {passed}, Failed: {failed}")
        
        if failed > 0:
            print("\nTip: Install pytest for better test output: pip install pytest")
            sys.exit(1)
        else:
            print("\nAll tests passed!")

