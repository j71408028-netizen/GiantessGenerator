# users/：使用者

> **这一组回答什么问题**：怎么把程序**跑起来**——安装、依赖、平台差异、已知限制。
> 面向的是「在装它的人」，不是「在改它的人」。

## 成员

| 文档 | 主要读者 | 性质 |
|---|---|---|
| [platforms/linux.md](platforms/linux.md) | Linux（X11 / XWayland）使用者 | 现状 |
| [platforms/linux-history.md](platforms/linux-history.md) | 需要溯源的人 | 历史归档（只读） |
| platforms/macos.md | macOS 使用者 | 占位（未开始） |

## 推荐阅读顺序

1. 常规安装：先读根 [`../../README.md`](../../README.md) 的「快速开始」。
2. **Linux 用户**：读 [platforms/linux.md](platforms/linux.md) §1–§2 确认支持范围与安装差异；
   遇到具体故障再查对应小节。
3. `linux-history.md` **不需要读**，只在追究「某个平台问题当初是怎么解决的」时按需查。

## 相关组

- 想理解某个机制**为什么这样设计**：见 [`../designs/README.md`](../designs/README.md)。
- 想在 Linux 上**开发**（跑自检、打包）：见 [`../dev/README.md`](../dev/README.md)
  与 [`../ops/release.md`](../ops/release.md)。
