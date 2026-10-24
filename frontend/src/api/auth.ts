import client from './client'

export interface AuthStatus {
  required: boolean
  authenticated: boolean
}

export interface LoginResult {
  required: boolean
  token: string | null
}

/** 是否启用了密码登录、当前会话是否已认证 */
export async function getStatus(): Promise<AuthStatus> {
  const { data } = await client.get<AuthStatus>('/auth/status')
  return data
}

/** 登录:成功返回 token;密码错误由 401 表达 */
export async function login(password: string): Promise<LoginResult> {
  const { data } = await client.post<LoginResult>('/auth/login', { password })
  return data
}

export async function logout(): Promise<void> {
  await client.post('/auth/logout')
}
