"""测试 split_all_punct 功能。"""
from __future__ import annotations

import pytest
from onepass.text_normalizer import TextNormConfig, split_sentences_with_rules, HARD_PUNCT, SOFT_PUNCT


def test_period_always_splits():
    """测试：句号必分句。"""
    text = "他顿了顿。于是继续说：事情还没完。别急。"
    cfg = TextNormConfig(split_all_punct=False)
    result = split_sentences_with_rules(text, cfg)
    
    # 应该按句号分句
    assert len(result) >= 3
    assert all(sent.strip()[-1] in HARD_PUNCT for sent in result if sent.strip())


def test_split_all_punct_false():
    """测试：split_all_punct=False 时，只按硬标点分句。"""
    text = "他顿了顿。于是继续说：事情还没完。别急。"
    cfg = TextNormConfig(split_all_punct=False)
    result = split_sentences_with_rules(text, cfg)
    
    # 应该只有硬标点分句
    # 预期: ["他顿了顿。", "于是继续说：事情还没完。", "别急。"]
    assert len(result) == 3
    assert result[0].strip().endswith("。")
    assert result[1].strip().endswith("。")
    assert result[2].strip().endswith("。")


def test_split_all_punct_true():
    """测试：split_all_punct=True 时，软标点也分句。"""
    text = "他顿了顿，于是继续说：事情还没完。别急。"
    cfg = TextNormConfig(split_all_punct=True, min_len=2)
    result = split_sentences_with_rules(text, cfg)
    
    # 应该按硬标点和软标点分句
    assert len(result) >= 3
    # 至少应该有硬标点分句
    hard_ending = sum(1 for sent in result if sent.strip() and sent.strip()[-1] in HARD_PUNCT)
    assert hard_ending >= 2  # 至少两个句号


def test_no_merge_across_hard_punct():
    """测试：不跨越硬切点合并。"""
    text = "短句。另一个短句。"
    cfg = TextNormConfig(split_all_punct=True, min_len=10)  # 设置较大的 min_len
    result = split_sentences_with_rules(text, cfg)
    
    # 即使两个都是短句，也不应该合并（因为中间有硬标点）
    assert len(result) >= 2
    # 每个句子都应该以硬标点结尾
    for sent in result:
        if sent.strip():
            assert sent.strip()[-1] in HARD_PUNCT


def test_hard_collapse_whitespace():
    """测试：硬清空白后跨行数字保留。"""
    from onepass.text_normalizer import hard_collapse_whitespace
    
    text = "ABC\n123。"
    collapsed = hard_collapse_whitespace(text)
    assert "ABC" in collapsed
    assert "123" in collapsed
    assert "。" in collapsed
    # 应该变成单个空格分隔
    assert "\n" not in collapsed
    
    # 分句应该正常工作
    cfg = TextNormConfig(split_all_punct=False)
    result = split_sentences_with_rules(collapsed, cfg)
    assert len(result) >= 1
    assert any("123" in sent for sent in result)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])





