# Prompt 结构

CLI 默认启用结构化 augmentation：

```text
Use case: ...
Primary request: ...
Scene/background: ...
Subject: ...
Style/medium: ...
Composition/framing: ...
Lighting/mood: ...
Color palette: ...
Materials/textures: ...
Text (verbatim): "..."
Constraints: ...
Avoid: ...
```

只有 `Primary request` 必定存在，其他段落按参数加入。对应参数为：

`--use-case`, `--scene`, `--subject`, `--style`, `--composition`, `--lighting`, `--palette`, `--materials`, `--text`, `--constraints`, `--negative`。

使用 `--no-augment` 可原样发送 prompt。`--prompt-file` 适合长 prompt，并且与 positional prompt、`--prompt` 互斥。

## 生成 prompt

优先按以下顺序描述：主体、动作、场景、构图/镜头、光线、材质/风格、文字、硬约束、避免项。不要用冗长形容词代替可观察要求。

## 编辑 prompt

编辑时明确：

1. 目标参考图和目标区域；
2. 要改变的属性；
3. 必须保持的身份、服装、姿态、相机、背景或光线；
4. 新旧元素之间的遮挡和物理关系。

多参考图时给图片分配稳定角色，例如“图 1 是待编辑场景，图 2 是人物设定”。

## 文字渲染

需要图片内文字时，把准确文字放在 `--text`，并在 constraints 中写位置、字体气质、大小写和“不添加其他文字”。生成后必须视觉检查拼写。

## 透明背景

模型 policy 支持 `background` 和 `output_format` 时，可请求 `--background transparent --output-format png`。不支持原生透明时，先要求纯色无渐变背景，再用 `chroma-key` 转 alpha。
