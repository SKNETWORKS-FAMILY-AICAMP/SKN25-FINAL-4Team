const pulse = `
  @keyframes sk-pulse {
    0%,100% { opacity: .45; }
    50%      { opacity: .15; }
  }
`
const B = (w='100%', h=16, extra={}) => ({
  width: w, height: h, borderRadius: 8,
  background: 'var(--border, #e5e7eb)',
  animation: 'sk-pulse 1.4s ease-in-out infinite',
  flexShrink: 0,
  ...extra,
})

export default function PanelSkeleton() {
  return (
    <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16, height: '100%', boxSizing: 'border-box' }}>
      <style>{pulse}</style>

      {/* 상단 stat 카드 4개 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
        {[0,1,2,3].map(i => (
          <div key={i} style={{ padding: 16, borderRadius: 12, background: 'var(--surface,#fff)', border: '1px solid var(--border,#e5e7eb)', display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={B(60, 10)} />
            <div style={B('70%', 28)} />
            <div style={B('50%', 10)} />
          </div>
        ))}
      </div>

      {/* 차트 2개 */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, flex: 1, minHeight: 0 }}>
        {[0,1].map(i => (
          <div key={i} style={{ padding: 16, borderRadius: 12, background: 'var(--surface,#fff)', border: '1px solid var(--border,#e5e7eb)', display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={B('40%', 12)} />
            <div style={{ flex: 1, borderRadius: 8, background: 'var(--border,#e5e7eb)', animation: 'sk-pulse 1.4s ease-in-out infinite', animationDelay: `${i * 0.2}s` }} />
          </div>
        ))}
      </div>

      {/* 하단 리스트 행 3개 */}
      <div style={{ padding: 16, borderRadius: 12, background: 'var(--surface,#fff)', border: '1px solid var(--border,#e5e7eb)', display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={B('30%', 12)} />
        {[0,1,2].map(i => (
          <div key={i} style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            <div style={B(32, 32, { borderRadius: '50%', animationDelay: `${i*0.15}s` })} />
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={B('60%', 10, { animationDelay: `${i*0.15}s` })} />
              <div style={B('40%', 8, { animationDelay: `${i*0.15}s` })} />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
