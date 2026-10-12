// Composer attachments: client-side image downscaling (also converts HEIC photos,
// which Claude can't read) and raw-body upload. The server re-checks everything.

export type AttachmentMeta = { id: string; name: string; kind: 'image' | 'pdf' | 'text'; mime: string; size: number }

const MAX_EDGE = 1600
export const MAX_FILES = 4
export const ACCEPT = 'image/*,application/pdf,.txt,.md,.csv,.json,.gpx,.tcx'

async function downscale(file: File): Promise<Blob> {
  if (file.type === 'image/gif') return file // keep animation
  let bitmap: ImageBitmap
  try { bitmap = await createImageBitmap(file) } catch { return file }
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(bitmap.width * scale)
  canvas.height = Math.round(bitmap.height * scale)
  canvas.getContext('2d')!.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close()
  // Screenshots stay crisp as PNG when small; photos become JPEG.
  const png = file.type === 'image/png' && file.size < 1_500_000
  const out = await new Promise<Blob | null>((r) => canvas.toBlob(r, png ? 'image/png' : 'image/jpeg', 0.85))
  return out ?? file
}

export async function uploadAttachment(file: File): Promise<AttachmentMeta> {
  const isImage = file.type.startsWith('image/') || /\.(heic|heif)$/i.test(file.name)
  const body = isImage ? await downscale(file) : file
  let name = file.name || (isImage ? 'pasted-image.png' : 'attachment')
  if (isImage && body !== file) name = name.replace(/\.(heic|heif|png|jpe?g|webp)$/i, '') + (body.type === 'image/png' ? '.png' : '.jpg')
  const r = await fetch(`/app/api/attachments?name=${encodeURIComponent(name)}`, {
    method: 'POST', credentials: 'same-origin', body,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.error ?? `Upload failed (${r.status})`)
  return data as AttachmentMeta
}

export const attachmentUrl = (id: string) => `/app/api/attachments/${id}`

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
