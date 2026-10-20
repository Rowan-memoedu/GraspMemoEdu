# GraspMemoEdu 安装与 PWA 维护

发布页：https://rowan-memoedu.github.io/GraspMemoEdu-Pages/download.html 。学习入口仍为原网站，账号、Topic ID、权限和学习记录沿用云端系统。

- iPhone / iPad：Safari 打开发布页，分享 → 添加到主屏幕；如显示“作为网页 App 打开”，保持开启。
- 安卓：Chrome / Edge 打开发布页，使用安装按钮或浏览器菜单。部分设备只有主屏幕快捷方式，独立应用入口以真机结果为准。
- Windows：Edge / Chrome 安装站点应用，使用独立窗口、开始菜单和桌面入口。

本版没有 APK、IPA 或 EXE，不需要 Apple 开发者会员。安装方式不增加离线学习或系统通知能力。首次在独立应用中打开可能需要重新登录；同账号读取云端进度，游客身份不自动跨设备合并。

小米 / HyperOS 手机若在 Edge 点击安装后跳转到应用信息页，检查 Edge 的“权限 → 其他权限 → 桌面快捷方式（Home screen shortcuts）”。允许这一项后返回发布页重新安装；无需同时开启其他权限。

## 安卓真机结果

REDMI K90 Max / Android 16、Edge 153.0.4234.49 已完成 USB 真机验收。Edge 通过桌面固定入口运行 PWA；从真实桌面图标启动后，系统进入 `WebappActivity`，页面 `display-mode: standalone` 为真，没有生成独立 WebAPK 包。竖屏无横向溢出，系统键盘可输入且输入框可见，返回键能回到主页并退出，关闭后从图标重开仍保留同一游客身份。测试没有提交登录或学习答案；临时输入已清除，自动化辅助进程已退出，原有无障碍服务和自动旋转已恢复。

本机首次安装曾被 Edge 的桌面快捷方式权限拦截，允许该项后安装成功。iOS 和 Windows 的真实系统安装仍待各自设备验收；本轮没有另行发布新网页版本来重复测试升级。

## 资源与更新

`web/manifest.webmanifest` 的 id、start_url、scope 均使用相对路径，保持 GitHub Pages 子目录部署与已有身份来源一致。主页面和发布页共用 `pwa.js`，通过 HTTPS 注册同目录 `sw.js`。iOS 图标由 `apple-touch-icon` 提供；标准图标和独立 maskable 图标由 `scripts/build_pwa_icons.py` 生成。

Service Worker 只处理本应用目录下的 GET 页面导航，优先直接联网，失败时返回无缓存的重连提示。API、静态资源、POST 和其他来源不被拦截；不建立 Cache Storage，不缓存身份与课程，不排队重放答案。更新后立即接管，但不刷新当前页面，保留正在编辑的输入。网页自身仍遵循原 HTTP 缓存策略；正常重新打开或刷新后读取服务器提供的页面。

新增或修改 Web 资源后按 README 更新整套版本号、导出并运行 `Publish-Frontend.ps1`。PNG 以 GitHub blob base64 上传，不能作为文本写入。公开文件清单必须显式包含新增的 PWA 文件，不能放宽为任意目录上传。

## 验收

1. Python `tests/test_pwa.py` 检查子路径解析与真实图标尺寸；Node `tests/pwa-worker.test.cjs` 验证导航、断网、API 和写入边界。
2. `node scripts/pwa_acceptance.mjs <静态导出目录> <证据目录>` 在独立 Chrome 配置目录中验证浏览器识别、五种宽度、断网恢复、更新保留输入以及安装提示状态。模拟安装事件只验证按钮逻辑，不代表完成系统安装。
3. 上线后运行 `python scripts/verify_pwa_publication.py <静态导出目录> <公开网站URL> <回读JSON> --version <资源版本>`，核对每个公开文件的字节哈希。再对公开 URL 运行同一浏览器验收，验证真实 API 首页和用户菜单安装入口，不提交学习答案。
4. 独立安装真机验收另行进行：安卓连接 USB 后检查真实安装、启动模式、软键盘、返回键、关闭重开和升级；iOS / Windows 实际安装同样不能用浏览器自动化结果替代。

仅使用隔离 Chrome 配置和测试身份，不读取用户当前浏览器资料或消耗正式学习进度。回退使用上一份已核验静态发布物，保持相同网站来源和 manifest id；不删除身份存储或修改云端学习数据。
