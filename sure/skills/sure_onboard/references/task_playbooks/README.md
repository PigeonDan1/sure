# Task Playbooks

本目录存放 SURE-EVAL 模型接入的任务级补充规范。`../AGENTS.md` 仍然是
模型 onboarding 的总规范；新模型扩充时必须先读总规范，再读对应任务文档。

当前第一版任务文档：

| 任务 | 文档 | 适用范围 |
|------|------|----------|
| ASR / Streaming ASR | [ASR.md](ASR.md) | 离线 ASR、流式 ASR、X-ASR、sherpa-onnx |
| Speech Understanding | [SPEECH_UNDERSTANDING.md](SPEECH_UNDERSTANDING.md) | ASR、S2TT、SER、SLU、GR 多任务语音理解模型 |
| TTS | [TTS.md](TTS.md) | F5-TTS、IndexTTS-2、带音色参考的语音合成 |
| VC | [VC.md](VC.md) | Seed-VC、音频到音频的 voice conversion |
| KWS | [KWS.md](KWS.md) | keyword spotting、WekWS、唤醒词模型 |

维护规则：

- 任务文档只写任务特有经验、坑点和检查表，不复制总状态机。
- 通用流程、artifact、Docker、checkpoint 规则继续维护在 `../AGENTS.md`。
- 新增任务类型时，在本目录新增 `{TASK}.md`，并更新本 README。
- 如果某次模型接入踩到可复用问题，优先补充到对应任务文档。
