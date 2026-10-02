import { useId } from 'react'

/**
 * VeWealth 品牌标识。
 * 以矢量「⌁ 信号波」延续原有识别：圆角方牌 + 品牌青渐变，波形为一条上升
 * 的平滑信号曲线，末端一枚亮点表示「信号命中」。纯 SVG 绘制，任意尺寸清晰。
 * 渐变 id 使用 useId() 保证同一页面多实例（如 /login 的 Navbar + 头部）不产生重复 id。
 */
export default function BrandMark({ size = 40 }: { size?: number }) {
  const gradientId = useId()

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 44 44"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className="shrink-0 drop-shadow-sm transition-transform duration-[var(--dur-base)] ease-[var(--ease-out)] group-hover:scale-105"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#0d9488" />
          <stop offset="100%" stopColor="#0f766e" />
        </linearGradient>
      </defs>
      <rect width="44" height="44" rx="13" fill={`url(#${gradientId})`} />
      <rect x="0.5" y="0.5" width="43" height="43" rx="12.5" stroke="rgba(255,255,255,0.22)" />
      <path
        d="M11 26 C13 18, 17 16, 20 21 C23 26, 27 24, 30 15"
        stroke="#fff"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="32.5" cy="12.5" r="2.4" fill="#5eead4" />
    </svg>
  )
}
