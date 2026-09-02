import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api';

export const apiClient = {
  getSubgraph: async (accountId, depth = 2) => {
    const { data } = await axios.get(`${API_BASE}/graph/subgraph`, {
      params: { account_id: accountId, depth }
    });
    return data;
  },

  getShortestPath: async (accountA, accountB) => {
    const { data } = await axios.get(`${API_BASE}/graph/shortest-path`, {
      params: { account_a: accountA, account_b: accountB }
    });
    return data;
  },

  getFlow: async (accountA, accountB, window = '7d') => {
    const { data } = await axios.get(`${API_BASE}/graph/flow`, {
      params: { account_a: accountA, account_b: accountB, window }
    });
    return data;
  },

  getAIReport: async (accountId) => {
    const { data } = await axios.get(`${API_BASE}/accounts/${accountId}/enrich`);
    return data;
  },
  evaluateRisk: async (accountId, params = {}) => {
    const { data } = await axios.post(
      `${API_BASE}/risk/evaluate/${accountId}`,
      null,
      { params }
    );
    return data;
  }
};