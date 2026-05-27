export function LoadingHint({ text }: { text: string }) {
  return (
    <div className="text-sm text-gray-500 bg-indigo-50 border border-indigo-100 rounded-lg px-3 py-2">
      {text}
    </div>
  )
}
