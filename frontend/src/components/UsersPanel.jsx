export default function UsersPanel() {
  return (
    <div style={{ padding: '24px 32px', color: 'var(--text)', height: '100%', background: 'var(--bg)' }}>
      <h2 style={{ marginBottom: 8 }}>👥 사용자 관리 (User Management)</h2>
      <p style={{ color: 'var(--text3)', marginBottom: 24, fontSize: 14 }}>
        플랫폼을 이용하는 테넌트 내 관리자 및 운영자 계정을 관리합니다. (현재 시연을 위한 준비 중 화면입니다)
      </p>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>등록된 사용자 목록</h3>
          <button style={{ background: '#2563eb', border: 'none', color: '#fff', padding: '6px 14px', borderRadius: 6, cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>+ 사용자 추가</button>
        </div>
        
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14, textAlign: 'left' }}>
          <thead>
            <tr style={{ background: 'var(--bg)', borderBottom: '1px solid var(--border)', color: 'var(--text3)', fontSize: 12 }}>
              <th style={{ padding: '12px 20px', fontWeight: 600 }}>이름</th>
              <th style={{ padding: '12px 20px', fontWeight: 600 }}>이메일</th>
              <th style={{ padding: '12px 20px', fontWeight: 600 }}>역할 (Role)</th>
              <th style={{ padding: '12px 20px', fontWeight: 600 }}>접속 권한</th>
            </tr>
          </thead>
          <tbody>
            <tr style={{ borderBottom: '1px solid var(--line)' }}>
              <td style={{ padding: '14px 20px', fontWeight: 600 }}>공장장 (Admin)</td>
              <td style={{ padding: '14px 20px', color: 'var(--text3)' }}>admin@honda-rd.eu</td>
              <td style={{ padding: '14px 20px' }}>
                <span style={{ color: '#2563eb', background: '#2563eb22', padding: '4px 10px', borderRadius: 6, fontSize: 12, fontWeight: 600 }}>최고 관리자</span>
              </td>
              <td style={{ padding: '14px 20px' }}>
                <span style={{ color: '#3fb950', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                  <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#3fb950' }}/> 활성
                </span>
              </td>
            </tr>
            <tr>
              <td style={{ padding: '14px 20px', fontWeight: 600 }}>운영팀 A</td>
              <td style={{ padding: '14px 20px', color: 'var(--text3)' }}>op_team_a@honda-rd.eu</td>
              <td style={{ padding: '14px 20px' }}>
                <span style={{ color: '#d29922', background: '#d2992222', padding: '4px 10px', borderRadius: 6, fontSize: 12, fontWeight: 600 }}>뷰어 (읽기 전용)</span>
              </td>
              <td style={{ padding: '14px 20px' }}>
                <span style={{ color: '#3fb950', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                  <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#3fb950' }}/> 활성
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}
