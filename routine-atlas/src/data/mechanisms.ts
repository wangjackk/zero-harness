/* 表里这一小撮名字，以及它们各自是什么。
   没有「工具类」和「agent 类」两种东西 —— 只有一种单元，kind 只是这一处借给页面看的标签。 */

export type Kind = 'agent' | 'tool'
export type GroupId = 'core' | 'file' | 'shell' | 'web'

export interface Mech {
  name: string
  kind: Kind
  group: GroupId
  role: string
  one: string
  source: string
}

export const MECHS: Mech[] = [
  {
    name: 'agent',
    kind: 'agent',
    group: 'core',
    role: '主循环',
    one: '每轮依次组装上下文、请求模型、调用模型指定的单元并回填结果，直至模型不再发起调用。',
    source: '单轮处理流程'
  },
  {
    name: 'Read',
    kind: 'tool',
    group: 'file',
    role: '读文件',
    one: '按行读取指定范围，避免将整个文件加入上下文。',
    source: 'tools/file_ops/ReadTool/ReadTool.py'
  },
  {
    name: 'Write',
    kind: 'tool',
    group: 'file',
    role: '写文件',
    one: '写入完整文件；写入前要求调用方已读取该文件。',
    source: 'tools/file_ops/WriteTool/WriteTool.py'
  },
  {
    name: 'Edit',
    kind: 'tool',
    group: 'file',
    role: '改文件',
    one: '按原文定位，仅在唯一匹配时执行替换，不使用模糊匹配。',
    source: 'tools/file_ops/EditTool/EditTool.py'
  },
  {
    name: 'Grep',
    kind: 'tool',
    group: 'file',
    role: '搜内容',
    one: '在文件内容中搜索指定文本，返回带行号的匹配行。',
    source: 'tools/file_ops/GrepTool/GrepTool.py'
  },
  {
    name: 'Glob',
    kind: 'tool',
    group: 'file',
    role: '找文件',
    one: '按路径模式查找文件，不读取文件内容。',
    source: 'tools/file_ops/GlobTool/GlobTool.py'
  },
  {
    name: 'Bash',
    kind: 'tool',
    group: 'shell',
    role: '执行命令',
    one: '在系统 shell 中执行命令，合并返回 stdout 和 stderr。',
    source: 'tools/shell/BashTool/BashTool.py'
  },
  {
    name: 'WebSearch',
    kind: 'tool',
    group: 'web',
    role: '联网搜索',
    one: '提交查询并返回候选结果的标题和摘要。',
    source: 'tools/remote/WebSearchTool/WebSearchTool.py'
  },
  {
    name: 'WebFetch',
    kind: 'tool',
    group: 'web',
    role: '获取网页',
    one: '读取指定 URL，并返回可处理的文本内容。',
    source: 'tools/remote/WebFetchTool/WebFetchTool.py'
  }
]
