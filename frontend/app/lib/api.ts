/**
 * Resolve the backend API base URL at runtime.
 *
 * If NEXT_PUBLIC_API_URL is set at build time it takes priority.
 * Otherwise the convention is: same hostname as the frontend, port 8001.
 * This keeps a single build working on localhost and any production host.
 */
export function getApiBaseUrl(): string {
  // Build-time override (inlined by Next.js at build time)
  const configured = process.env.NEXT_PUBLIC_API_URL
  if (configured && configured !== '') return configured

  // Runtime resolution — replace frontend port with backend port
  if (typeof window !== 'undefined') {
    const { protocol, hostname } = window.location
    return `${protocol}//${hostname}:8001`
  }

  // SSR fallback
  return 'http://localhost:8001'
}
