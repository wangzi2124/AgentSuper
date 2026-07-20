// 带超时功能的 fetch 封装
export async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  _timeout?: number,
): Promise<Response> {
  return fetch(url, options)
}
