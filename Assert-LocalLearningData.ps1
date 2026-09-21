function Assert-LocalLearningData {
    param([Parameter(Mandatory)][string]$DataDirectory)
    $marker = Join-Path $DataDirectory 'cloud-authoritative.json'
    if (Test-Path -LiteralPath $marker -PathType Leaf) {
        throw '此目录已是云端迁移回滚快照，禁止启动旧后端或维护任务。正式服务在腾讯云；本地开发请指定独立 DataDirectory。详见 docs/tencent-deployment.md。'
    }
}
