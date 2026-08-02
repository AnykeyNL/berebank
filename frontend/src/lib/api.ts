const TOKEN_KEY = 'berebank_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token === null) localStorage.removeItem(TOKEN_KEY)
  else localStorage.setItem(TOKEN_KEY, token)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const resp = await fetch(`/api${path}`, {
    ...options,
    headers,
    signal: options.signal ?? AbortSignal.timeout(15_000),
  })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const body = await resp.json()
      if (typeof body.detail === 'string') detail = body.detail
      else if (Array.isArray(body.detail)) detail = body.detail[0]?.msg ?? detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(resp.status, detail)
  }
  if (resp.status === 204) return undefined as T
  const text = await resp.text()
  if (!text) return undefined as T
  return JSON.parse(text) as T
}

function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function readError(resp: Response): Promise<string> {
  let detail = resp.statusText
  try {
    const body = await resp.json()
    if (typeof body.detail === 'string') detail = body.detail
    else if (Array.isArray(body.detail)) detail = body.detail[0]?.msg ?? detail
  } catch {
    /* not JSON */
  }
  return detail
}

export async function downloadApiFile(path: string, timeoutMs = 120_000): Promise<{ blob: Blob; filename: string }> {
  const resp = await fetch(`/api${path}`, {
    headers: authHeaders(),
    signal: AbortSignal.timeout(timeoutMs),
  })
  if (!resp.ok) throw new ApiError(resp.status, await readError(resp))
  const blob = await resp.blob()
  const disposition = resp.headers.get('Content-Disposition') ?? ''
  const match = disposition.match(/filename="([^"]+)"/)
  return { blob, filename: match?.[1] ?? 'download.json' }
}

export async function uploadApiFile<T>(
  path: string,
  file: File,
  timeoutMs = 120_000,
): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const resp = await fetch(`/api${path}`, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
    signal: AbortSignal.timeout(timeoutMs),
  })
  if (!resp.ok) throw new ApiError(resp.status, await readError(resp))
  return resp.json() as Promise<T>
}
