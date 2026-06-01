export default function SettingsPanel() {
  return (
    <div style={{ padding: '24px 32px', color: 'var(--text)', height: '100%', background: 'var(--bg)' }}>
      <h2 style={{ marginBottom: 8 }}>⚙️ 시스템 설정 (System Settings)</h2>
      <p style={{ color: 'var(--text3)', marginBottom: 24, fontSize: 14 }}>
        다중 기업 지원(Multi-Tenant)을 위한 테넌트 환경 설정 페이지입니다. (현재 시연을 위한 준비 중 화면입니다)
      </p>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 24, marginBottom: 20 }}>
        <h3 style={{ marginTop: 0, marginBottom: 8, color: '#2563eb' }}>기업 프로필 설정</h3>
        <p style={{ color: 'var(--text3)', fontSize: 13, marginBottom: 20 }}>엔터프라이즈 플랫폼에 표시될 기업명과 로고를 설정합니다.</p>
        
        <div style={{ marginBottom: 16 }}>
          <label style={{ display: 'block', fontSize: 13, marginBottom: 8, fontWeight: 600 }}>기업명 (Tenant Name)</label>
          <input type="text" disabled value="Honda R&D Europe GmbH" style={{ width: '100%', maxWidth: 400, padding: '10px 14px', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text3)', borderRadius: 6 }} />
        </div>
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 24 }}>
        <h3 style={{ marginTop: 0, marginBottom: 8, color: '#a371f7' }}>보안 & sLLM 연동 설정</h3>
        <p style={{ color: 'var(--text3)', fontSize: 13, marginBottom: 20 }}>On-Premise 보안 모델 연동을 위한 API Key 및 내부망 엔드포인트를 설정합니다.</p>
        
        <div style={{ marginBottom: 16 }}>
          <label style={{ display: 'block', fontSize: 13, marginBottom: 8, fontWeight: 600 }}>엔드포인트 URL (Internal Endpoint)</label>
          <input type="text" disabled value="http://localhost:11434/api/generate" style={{ width: '100%', maxWidth: 400, padding: '10px 14px', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text3)', borderRadius: 6 }} />
        </div>

        <div style={{ marginBottom: 16 }}>
          <label style={{ display: 'block', fontSize: 13, marginBottom: 8, fontWeight: 600 }}>sLLM 모델 선택</label>
          <select disabled style={{ width: '100%', maxWidth: 400, padding: '10px 14px', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text3)', borderRadius: 6 }}>
            <option>Llama-3-Korean-8B (Local)</option>
          </select>
        </div>
      </div>
    </div>
  )
}
