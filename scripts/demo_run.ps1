# 一键运行 OnePass Audio 最小演示流程，无需管理员权限。
# 若需传递其他参数，可直接在此脚本中追加到 python 命令之后。
python scripts/smoke_test.py
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
