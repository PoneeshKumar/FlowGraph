export const GRAPH_NODES = [
  { id: 'hub',  type: 'account',  risk: 'critical', label: 'ACC-4471', x: 300, y: 248, volume: 2400000 },
  { id: 'src1', type: 'bank',     risk: 'medium',   label: 'BNK-1102', x: 82,  y: 92,  volume: 380000 },
  { id: 'src2', type: 'account',  risk: 'low',      label: 'ACC-8832', x: 58,  y: 208, volume: 420000 },
  { id: 'src3', type: 'account',  risk: 'high',     label: 'ACC-2291', x: 74,  y: 322, volume: 610000 },
  { id: 'src4', type: 'merchant', risk: 'low',      label: 'MRC-5503', x: 138, y: 420, volume: 175000 },
  { id: 'src5', type: 'exchange', risk: 'medium',   label: 'EXC-9017', x: 172, y: 52,  volume: 290000 },
  { id: 'src6', type: 'account',  risk: 'high',     label: 'ACC-3357', x: 55,  y: 440, volume: 520000 },
  { id: 'int1', type: 'account',  risk: 'high',     label: 'ACC-7741', x: 190, y: 170, volume: 890000 },
  { id: 'int2', type: 'account',  risk: 'critical', label: 'ACC-6612', x: 212, y: 308, volume: 1100000 },
  { id: 'int3', type: 'account',  risk: 'medium',   label: 'ACC-9980', x: 198, y: 392, volume: 340000 },
  { id: 'dst1', type: 'exchange', risk: 'critical', label: 'EXC-0044', x: 458, y: 148, volume: 1550000 },
  { id: 'dst2', type: 'account',  risk: 'high',     label: 'ACC-1129', x: 472, y: 370, volume: 870000 },
  { id: 'ext1', type: 'bank',     risk: 'low',      label: 'BNK-3301', x: 370, y: 78,  volume: 220000 },
  { id: 'ext2', type: 'merchant', risk: 'medium',   label: 'MRC-8814', x: 402, y: 432, volume: 158000 },
]

export const GRAPH_EDGES = [
  { id: 'e1',  from: 'src1', to: 'int1', weight: 380000,  active: true,  isCycle: false },
  { id: 'e2',  from: 'src2', to: 'int1', weight: 420000,  active: false, isCycle: false },
  { id: 'e3',  from: 'src5', to: 'int1', weight: 290000,  active: true,  isCycle: false },
  { id: 'e4',  from: 'src3', to: 'int2', weight: 610000,  active: true,  isCycle: false },
  { id: 'e5',  from: 'src6', to: 'int2', weight: 520000,  active: false, isCycle: false },
  { id: 'e6',  from: 'src4', to: 'int3', weight: 175000,  active: false, isCycle: false },
  { id: 'e7',  from: 'int1', to: 'hub',  weight: 1090000, active: true,  isCycle: false },
  { id: 'e8',  from: 'int2', to: 'hub',  weight: 1130000, active: true,  isCycle: false },
  { id: 'e9',  from: 'int3', to: 'hub',  weight: 340000,  active: false, isCycle: false },
  { id: 'e10', from: 'hub',  to: 'dst1', weight: 1550000, active: true,  isCycle: false },
  { id: 'e11', from: 'hub',  to: 'dst2', weight: 870000,  active: true,  isCycle: false },
  { id: 'e12', from: 'dst1', to: 'ext1', weight: 220000,  active: false, isCycle: false },
  { id: 'e13', from: 'dst2', to: 'ext2', weight: 158000,  active: false, isCycle: false },
  { id: 'e14', from: 'dst1', to: 'int2', weight: 180000,  active: true,  isCycle: true  },
]

export const RECENT_ALERTS = [
  {
    id: 'ALT-001',
    severity: 'critical',
    type: 'Cycle Detected',
    message: '4-hop cycle: ACC-4471 → EXC-0044 → ACC-6612 → ACC-4471',
    account: 'ACC-4471',
    amount: 180000,
    timestamp: '2 min ago',
    confidence: 97,
    aiExplanation: 'Account ACC-4471 received 14 inbound transfers from 6 sources totaling $2.4M in 18 hours, then routed funds back through EXC-0044 and ACC-6612 — a classic layering cycle. Average hop time: 4 minutes.',
  },
  {
    id: 'ALT-002',
    severity: 'critical',
    type: 'Hub Centrality Spike',
    message: 'ACC-4471 centrality score jumped 340% in 6h',
    account: 'ACC-4471',
    amount: 2400000,
    timestamp: '8 min ago',
    confidence: 94,
    aiExplanation: 'Central hub with 6 inbound sources and 2 outbound destinations. PageRank centrality score places this account in the top 0.01% of the network.',
  },
  {
    id: 'ALT-003',
    severity: 'high',
    type: 'Structuring Pattern',
    message: 'ACC-2291 split $610K across 9 sub-$10K transactions',
    account: 'ACC-2291',
    amount: 610000,
    timestamp: '22 min ago',
    confidence: 88,
    aiExplanation: 'Classic structuring: nine transactions each under the $10,000 CTR reporting threshold, all sent within a 45-minute window to the same intermediate account.',
  },
  {
    id: 'ALT-004',
    severity: 'high',
    type: 'Rapid Succession',
    message: 'ACC-3357 → ACC-6612: 8 transfers in 12 minutes',
    account: 'ACC-3357',
    amount: 520000,
    timestamp: '35 min ago',
    confidence: 82,
    aiExplanation: 'Velocity anomaly: 8 wire transfers totaling $520K within a 12-minute window, far exceeding the account\'s 90-day average of 2 transactions per week.',
  },
  {
    id: 'ALT-005',
    severity: 'medium',
    type: 'Community Risk Flag',
    message: 'EXC-9017 shares community with 3 flagged accounts',
    account: 'EXC-9017',
    amount: 290000,
    timestamp: '1 hr ago',
    confidence: 71,
    aiExplanation: 'Louvain community detection placed EXC-9017 in the same cluster as 3 accounts with existing fraud flags. Elevated risk by association.',
  },
]

export const RECENT_TRANSACTIONS = [
  { id: 'TXN-88421', from: 'ACC-4471', to: 'EXC-0044', amount: 420000,  currency: 'USD', rail: 'Wire',  risk: 'critical', ts: '14:32:01', status: 'flagged'   },
  { id: 'TXN-88419', from: 'ACC-6612', to: 'ACC-4471', amount: 610000,  currency: 'USD', rail: 'ACH',   risk: 'critical', ts: '14:31:47', status: 'flagged'   },
  { id: 'TXN-88417', from: 'ACC-2291', to: 'ACC-6612', amount: 85000,   currency: 'USD', rail: 'Wire',  risk: 'high',     ts: '14:31:22', status: 'flagged'   },
  { id: 'TXN-88415', from: 'BNK-1102', to: 'ACC-7741', amount: 380000,  currency: 'USD', rail: 'Wire',  risk: 'medium',   ts: '14:30:58', status: 'reviewing' },
  { id: 'TXN-88413', from: 'EXC-9017', to: 'ACC-7741', amount: 290000,  currency: 'USD', rail: 'Crypto',risk: 'medium',   ts: '14:30:41', status: 'reviewing' },
  { id: 'TXN-88411', from: 'ACC-8832', to: 'ACC-7741', amount: 138000,  currency: 'USD', rail: 'ACH',   risk: 'low',      ts: '14:30:09', status: 'cleared'   },
  { id: 'TXN-88408', from: 'MRC-5503', to: 'ACC-9980', amount: 48000,   currency: 'USD', rail: 'Card',  risk: 'low',      ts: '14:29:55', status: 'cleared'   },
  { id: 'TXN-88404', from: 'ACC-3357', to: 'ACC-6612', amount: 65000,   currency: 'USD', rail: 'Wire',  risk: 'high',     ts: '14:29:30', status: 'flagged'   },
  { id: 'TXN-88401', from: 'EXC-0044', to: 'BNK-3301', amount: 220000,  currency: 'USD', rail: 'Wire',  risk: 'critical', ts: '14:29:12', status: 'frozen'    },
  { id: 'TXN-88398', from: 'ACC-1129', to: 'MRC-8814', amount: 42000,   currency: 'USD', rail: 'Card',  risk: 'medium',   ts: '14:28:50', status: 'reviewing' },
]

export const METRICS = {
  volume24h:     { value: 8420000,  delta: +14.2 },
  activeAccounts:{ value: 1847,     delta: +3.1  },
  cyclesDetected:{ value: 3,        delta: +200  },
  riskAlerts:    { value: 17,       delta: +42.9 },
}

// ── In-Flight: ACH Batches ──────────────────────────────────────────────────
export const ACH_BATCHES = [
  {
    id: 'BATCH-20240530-001',
    filename: 'NACHA-20240530-001.ach',
    txnCount: 412,
    totalAmount: 2340000,
    returnCount: 0,
    submittedAt: '09:02 AM',
    status: 'processing',
    risk: 'medium',
    transactions: [
      { id: 'ACH-00412', from: 'BNK-1102', to: 'ACC-7741', amount: 38000,  returnCode: null,  risk: 'medium', status: 'processing' },
      { id: 'ACH-00411', from: 'ACC-8832', to: 'ACC-7741', amount: 138000, returnCode: null,  risk: 'low',    status: 'processing' },
      { id: 'ACH-00410', from: 'MRC-5503', to: 'ACC-9980', amount: 48000,  returnCode: null,  risk: 'low',    status: 'processing' },
      { id: 'ACH-00409', from: 'ACC-2291', to: 'ACC-6612', amount: 85000,  returnCode: null,  risk: 'high',   status: 'processing' },
      { id: 'ACH-00408', from: 'ACC-3357', to: 'ACC-6612', amount: 65000,  returnCode: null,  risk: 'high',   status: 'processing' },
    ],
  },
  {
    id: 'BATCH-20240530-002',
    filename: 'NACHA-20240530-002.ach',
    txnCount: 188,
    totalAmount: 940000,
    returnCount: 7,
    submittedAt: '11:45 AM',
    status: 'partially_returned',
    risk: 'high',
    transactions: [
      { id: 'ACH-00390', from: 'EXC-9017', to: 'ACC-4471', amount: 130000, returnCode: 'R07', risk: 'high',     status: 'returned' },
      { id: 'ACH-00389', from: 'ACC-1129', to: 'MRC-8814', amount: 42000,  returnCode: 'R01', risk: 'medium',   status: 'returned' },
      { id: 'ACH-00388', from: 'BNK-3301', to: 'EXC-0044', amount: 75000,  returnCode: null,  risk: 'critical', status: 'processing' },
      { id: 'ACH-00387', from: 'ACC-9980', to: 'ACC-4471', amount: 95000,  returnCode: null,  risk: 'medium',   status: 'processing' },
    ],
  },
  {
    id: 'BATCH-20240530-003',
    filename: 'NACHA-20240530-003.ach',
    txnCount: 64,
    totalAmount: 310000,
    returnCount: 0,
    submittedAt: '01:18 PM',
    status: 'submitted',
    risk: 'low',
    transactions: [
      { id: 'ACH-00372', from: 'MRC-8814', to: 'ACC-1129', amount: 21000, returnCode: null, risk: 'low', status: 'submitted' },
      { id: 'ACH-00371', from: 'BNK-1102', to: 'ACC-8832', amount: 54000, returnCode: null, risk: 'low', status: 'submitted' },
    ],
  },
]

// ── In-Flight: Wire Transactions ────────────────────────────────────────────
export const WIRE_INFLIGHT = [
  { id: 'WIR-44821', from: 'ACC-4471', to: 'EXC-0044', amount: 420000, currency: 'USD', submittedAt: '14:31', ageMin: 12,  risk: 'critical', status: 'pending', swift: 'CHASUS33' },
  { id: 'WIR-44819', from: 'ACC-6612', to: 'ACC-4471', amount: 610000, currency: 'USD', submittedAt: '14:29', ageMin: 14,  risk: 'critical', status: 'pending', swift: 'BOFAUS3N' },
  { id: 'WIR-44815', from: 'EXC-0044', to: 'BNK-3301', amount: 220000, currency: 'USD', submittedAt: '14:27', ageMin: 16,  risk: 'high',     status: 'pending', swift: 'CITIUS33' },
  { id: 'WIR-44801', from: 'ACC-7741', to: 'ACC-4471', amount: 280000, currency: 'USD', submittedAt: '13:55', ageMin: 48,  risk: 'high',     status: 'pending', swift: 'WFBIUS6S' },
  { id: 'WIR-44788', from: 'BNK-1102', to: 'EXC-9017', amount: 95000,  currency: 'USD', submittedAt: '12:10', ageMin: 113, risk: 'medium',   status: 'delayed', swift: 'CHASUS33' },
]

// ── In-Flight: Card Authorizations ──────────────────────────────────────────
// ageMin = minutes since authorization
export const CARD_AUTHS = [
  { id: 'AUTH-77421', merchant: 'MRC-5503', account: 'ACC-8832', amount: 1240,  currency: 'USD', ageMin: 4,   network: 'Visa',       status: 'authorized', risk: 'low'    },
  { id: 'AUTH-77418', merchant: 'MRC-8814', account: 'ACC-1129', amount: 8800,  currency: 'USD', ageMin: 11,  network: 'Mastercard', status: 'authorized', risk: 'low'    },
  { id: 'AUTH-77410', merchant: 'MRC-5503', account: 'ACC-9980', amount: 3400,  currency: 'USD', ageMin: 28,  network: 'Visa',       status: 'authorized', risk: 'medium' },
  { id: 'AUTH-77401', merchant: 'MRC-8814', account: 'ACC-4471', amount: 12000, currency: 'USD', ageMin: 74,  network: 'Visa',       status: 'authorized', risk: 'high'   },
  { id: 'AUTH-77388', merchant: 'MRC-5503', account: 'ACC-2291', amount: 9750,  currency: 'USD', ageMin: 142, network: 'Mastercard', status: 'stale',      risk: 'high'   },
  { id: 'AUTH-77371', merchant: 'MRC-8814', account: 'ACC-3357', amount: 4200,  currency: 'USD', ageMin: 310, network: 'Visa',       status: 'stale',      risk: 'critical'},
]

export const ACH_RETURN_CODES = {
  R01: 'Insufficient Funds',
  R02: 'Account Closed',
  R03: 'No Account / Unable to Locate',
  R04: 'Invalid Account Number',
  R07: 'Authorization Revoked',
  R10: 'Customer Advises Unauthorized',
  R16: 'Account Frozen',
  R29: 'Corporate Customer Advises Not Authorized',
}
