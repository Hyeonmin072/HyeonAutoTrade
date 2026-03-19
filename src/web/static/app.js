/**
 * AutoCoinTrade 대시보드 - 프론트엔드
 */
const API_BASE = '/api';
const REFRESH_INTERVAL = 1500;      // 전체 새로고침 (1.5초)
const PRICE_REFRESH_INTERVAL = 1000; // 실시간 가격/잔고/인사이트 (1초)

function fmt(num, decimals = 2) {
  if (num == null) return '-';
  return Number(num).toLocaleString('ko-KR', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtPrice(num) {
  if (num == null) return '-';
  const n = Number(num);
  return fmt(n, 0) + '원';
}

function fmtPercent(num) {
  if (num == null) return '-';
  const n = Number(num);
  const s = n >= 0 ? '+' : '';
  return s + n.toFixed(2) + '%';
}

async function fetchApi(path) {
  const res = await fetch(API_BASE + path);
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}

function setStatus(running, error = false) {
  const badge = document.getElementById('statusBadge');
  const text = badge.querySelector('.status-text');
  badge.className = 'status-badge';
  if (error) {
    badge.classList.add('error');
    text.textContent = '연결 실패';
  } else if (running) {
    badge.classList.add('running');
    text.textContent = '실행 중';
  } else {
    badge.classList.add('stopped');
    text.textContent = '중지됨';
  }
}

function renderStatus(data) {
  const el = document.getElementById('statusInfo');
  if (!data) return;
  const modeStr = data.futures_mode ? `선물 ${data.leverage || 1}x` : '현물';
  el.innerHTML = `
    <dt>거래소</dt><dd>${data.exchange || '-'}</dd>
    <dt>전략</dt><dd>${data.strategy || '-'}</dd>
    <dt>모드</dt><dd>${data.dry_run ? '시뮬레이션' : '실거래'}</dd>
    <dt>거래유형</dt><dd>${modeStr}</dd>
    <dt>AI</dt><dd>${data.ai_enabled ? '활성' : '비활성'}</dd>
  `;
  setStatus(data.running);
}

function renderStats(data) {
  const el = document.getElementById('statsInfo');
  if (!data) return;
  const realized = data.total_pnl ?? 0;
  const unrealized = data.total_unrealized_pnl ?? 0;
  const pnlClass = (realized + unrealized) >= 0 ? 'positive' : 'negative';
  const returnPct = data.total_return_percent ?? 0;
  const returnClass = returnPct >= 0 ? 'positive' : 'negative';
  el.innerHTML = `
    <dt>잔고</dt><dd>${fmt(data.balance, 0)}</dd>
    <dt>초기잔고</dt><dd>${fmt(data.initial_balance, 0)}</dd>
    <dt>포지션</dt><dd>${data.open_positions ?? 0}개</dd>
    <dt>실현손익</dt><dd class="${realized >= 0 ? 'positive' : 'negative'}">${fmt(realized, 2)}</dd>
    <dt>미실현손익</dt><dd class="${unrealized >= 0 ? 'positive' : 'negative'}">${fmt(unrealized, 2)}</dd>
    <dt>총 수익률</dt><dd class="${returnClass}">${fmtPercent(returnPct)}</dd>
  `;
}

function renderPrices(data) {
  const grid = document.getElementById('priceGrid');
  if (!data || Object.keys(data).length === 0) {
    grid.innerHTML = '<p class="empty">가격 데이터 없음</p>';
    return;
  }
  grid.innerHTML = Object.entries(data).map(([symbol, info]) => {
    const ticker = info.ticker || {};
    const price = info.current ?? ticker.price;
    // 1분봉 모드: 1분 등락률, 아니면 24h 등락률
    const change = info.change_percent_1m != null
      ? info.change_percent_1m
      : (ticker.change_percent_24h ?? ticker.change_24h ?? 0);
    const changeLabel = info.use_ohlcv ? '1분' : '24h';
    const changeClass = change >= 0 ? 'positive' : 'negative';
    const updatedAt = info.updated_at || ticker.timestamp;
    const timeStr = updatedAt ? new Date(updatedAt).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
    return `
      <div class="price-card">
        <div class="symbol">${symbol}</div>
        <div class="price">${fmtPrice(price)}</div>
        <div class="change ${changeClass}">${fmtPercent(change)} (${changeLabel})</div>
        ${timeStr ? `<div class="price-time">${timeStr}</div>` : ''}
      </div>
    `;
  }).join('');
}

function fmtAmount(currency, amount) {
  if (amount == null) return '-';
  const n = Number(amount);
  if (currency === 'KRW' || currency === 'USDT' || currency === 'USD') {
    return fmt(n, 0) + (currency === 'KRW' ? '원' : ' ' + currency);
  }
  return fmt(n, 8) + ' ' + currency;
}

function renderBalances(data) {
  const tbody = document.getElementById('balancesBody');
  if (!data || Object.keys(data).length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty">보유 코인 없음</td></tr>';
    return;
  }
  const items = Object.values(data).sort((a, b) => {
    if (a.currency === 'KRW') return -1;
    if (b.currency === 'KRW') return 1;
    return (b.total || 0) - (a.total || 0);
  });
  tbody.innerHTML = items.map(b => `
    <tr>
      <td class="mono"><strong>${b.currency}</strong></td>
      <td class="mono">${fmtAmount(b.currency, b.total)}</td>
      <td class="mono">${fmtAmount(b.currency, b.free)}</td>
      <td class="mono">${fmtAmount(b.currency, b.used)}</td>
    </tr>
  `).join('');
}

function renderPositions(data) {
  const tbody = document.getElementById('positionsBody');
  if (!data || data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">포지션 없음</td></tr>';
    return;
  }
  tbody.innerHTML = data.map(p => {
    const pnl = p.unrealized_pnl ?? 0;
    const pnlPct = p.unrealized_pnl_percent ?? 0;
    const pnlClass = pnl >= 0 ? 'positive' : 'negative';
    return `
      <tr>
        <td class="mono">${p.symbol}</td>
        <td class="mono">${fmt(p.entry_price)}</td>
        <td class="mono">${fmt(p.current_price)}</td>
        <td class="mono">${fmt(p.quantity)}</td>
        <td class="mono ${pnlClass}">${fmt(pnl)}</td>
        <td class="mono ${pnlClass}">${fmtPercent(pnlPct)}</td>
      </tr>
    `;
  }).join('');
}

function renderTrades(data) {
  const tbody = document.getElementById('tradesBody');
  if (!data || data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">거래 내역 없음</td></tr>';
    return;
  }
  tbody.innerHTML = data.map(t => {
    const ts = t.timestamp ? new Date(t.timestamp).toLocaleString('ko-KR') : '-';
    const pnl = t.pnl ?? 0;
    const pnlClass = pnl >= 0 ? 'positive' : 'negative';
    const meta = t.metadata || {};
    const reason = meta.reason || t.reason || '-';
    return `
      <tr>
        <td>${ts}</td>
        <td class="mono">${t.symbol}</td>
        <td><span class="${t.action === 'BUY' ? 'positive' : 'negative'}">${t.action}</span></td>
        <td class="mono">${fmt(t.quantity)}</td>
        <td class="mono">${fmt(t.price)}</td>
        <td class="mono ${pnlClass}">${t.pnl != null ? fmt(pnl) : '-'}</td>
        <td class="reason-cell">${escapeHtml(reason)}</td>
      </tr>
    `;
  }).join('');
}

async function refreshPrices() {
  try {
    const prices = await fetchApi('/prices');
    renderPrices(prices);
  } catch (err) {
    console.error('Price refresh failed:', err);
  }
}

async function refreshRealtime() {
  try {
    const [prices, stats, aiInsights, positions] = await Promise.all([
      fetchApi('/prices'),
      fetchApi('/stats'),
      fetchApi('/ai_insights'),
      fetchApi('/positions'),
    ]);
    renderPrices(prices);
    renderStats(stats);
    renderAiInsights(aiInsights);
    renderPositions(positions);
  } catch (err) {
    console.error('Realtime refresh failed:', err);
  }
}

function renderAiInsights(data) {
  const grid = document.getElementById('aiInsightsGrid');
  if (!data || Object.keys(data).length === 0) {
    grid.innerHTML = '<p class="empty">아직 분석 데이터 없음 (신호 생성 시 갱신)</p>';
    return;
  }
  grid.innerHTML = Object.entries(data).map(([symbol, info]) => {
    const m = info.metadata || {};
    const reason = info.reason || '-';
    const signalClass = (info.signal_type || 'hold').toLowerCase();
    const strength = info.strength != null ? (info.strength * 100).toFixed(0) + '%' : '';
    const sentiment = m.ai_sentiment || m.sentiment || '';
    const confidence = m.ai_confidence != null ? (m.ai_confidence * 100).toFixed(0) + '%' : '';
    const aiReason = m.ai_reason || '';
    const metaParts = [strength, sentiment, confidence].filter(Boolean);
    const aiInput = info.ai_input || {};
    const hasAiInput = aiInput.indicators_summary || aiInput.recent_prices;
    const skipReason = info.skip_reason || '';
    const hasDetails = reason !== '-' || aiReason || metaParts.length || hasAiInput || skipReason;
    const detailsHtml = hasDetails ? `
      <details class="insight-details">
        <summary>상세</summary>
        <div class="insight-details-content">
          ${skipReason ? `<div class="skip-reason">⚠ 미실행: ${escapeHtml(skipReason)}</div>` : ''}
          <div class="reason">${escapeHtml(reason)}</div>
          ${aiReason ? `<div class="ai-reason">[AI] ${escapeHtml(aiReason)}</div>` : ''}
          ${metaParts.length ? `<div class="meta">${metaParts.join(' · ')}</div>` : ''}
          ${hasAiInput ? `
            <div class="ai-input-section">
              <div class="ai-input-label">지표</div>
              <div class="ai-input-value">${escapeHtml(aiInput.indicators_summary || '-')}</div>
              ${aiInput.recent_prices ? `<div class="ai-input-label">최근 가격</div><div class="ai-input-value mono">${escapeHtml(aiInput.recent_prices)}</div>` : ''}
            </div>
          ` : ''}
        </div>
      </details>
    ` : '';
    return `
      <div class="insight-card">
        <div class="insight-header">
          <span class="symbol">${symbol}</span>
          <span class="signal-badge ${signalClass}">${info.signal_type || 'HOLD'}</span>
          ${skipReason ? '<span class="skip-badge" title="' + escapeHtml(skipReason) + '">⚠</span>' : ''}
        </div>
        ${detailsHtml}
      </div>
    `;
  }).join('');
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

async function refresh() {
  try {
    const [status, stats, prices, positions, trades, aiInsights, balances] = await Promise.all([
      fetchApi('/status'),
      fetchApi('/stats'),
      fetchApi('/prices'),
      fetchApi('/positions'),
      fetchApi('/trades?limit=20'),
      fetchApi('/ai_insights'),
      fetchApi('/balances'),
    ]);
    renderStatus(status);
    renderStats(stats);
    renderPrices(prices);
    renderBalances(balances);
    renderPositions(positions);
    renderTrades(trades);
    renderAiInsights(aiInsights);
  } catch (err) {
    console.error('Refresh failed:', err);
    setStatus(false, true);
  }
}

document.getElementById('stopBtn').addEventListener('click', async () => {
  try {
    await fetch(API_BASE + '/stop', { method: 'POST' });
    refresh();
  } catch (err) {
    console.error('Stop failed:', err);
  }
});

// 탭 전환
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`panel-${tab}`).classList.add('active');
  });
});

// 초기 로드 및 주기적 새로고침
refresh();
setInterval(refresh, REFRESH_INTERVAL);
// 실시간 데이터: 가격·잔고·손익·인사이트 1초마다
setInterval(refreshRealtime, PRICE_REFRESH_INTERVAL);
