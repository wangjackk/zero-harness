/** Agent 工具路径约定: 身份键 + 路径解析/展示 helper. */
import { resolve, isAbsolute, relative, sep } from 'node:path'

export const PROJECT_DIR_ROOT_PATH_KEY = 'project_dir_root_path'
/** agent push tool routine 时注入的调用方身份; 用 from_agent_id 避免跟 tool routine 自身的 agent_id 输入字段冲突. */
export const AGENT_ID_KEY = 'from_agent_id'

export function popProjectRoot(kwargs: Record<string, any>): string | null {
  const value = kwargs[PROJECT_DIR_ROOT_PATH_KEY]
  delete kwargs[PROJECT_DIR_ROOT_PATH_KEY]
  if (!value) return null
  return resolve(String(value))
}

export function resolveToolPath(path: string, projectRoot?: string | null): string {
  if (isAbsolute(path)) return resolve(path)
  return resolve(projectRoot || process.cwd(), path)
}

export function resolveOptionalToolPath(path: string | null | undefined,
                                         projectRoot?: string | null): string {
  if (path) return resolveToolPath(path, projectRoot)
  return resolve(projectRoot || process.cwd())
}

export function displayToolPath(path: string, projectRoot?: string | null): string {
  const root = resolve(projectRoot || process.cwd())
  const absolute = resolve(path)
  const rel = relative(root, absolute)
  if (rel === '.') return rel
  if (rel === '..' || rel.startsWith(`..${sep}`)) return absolute
  return rel
}
