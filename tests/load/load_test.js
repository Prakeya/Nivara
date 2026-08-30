/**
 * k6 Load Test: Nivara API
 *
 * Run: k6 run tests/load/load_test.js
 * Target: http://localhost:8000
 */

import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 10 },   // Ramp up to 10 VUs
    { duration: '1m', target: 10 },    // Stay at 10 VUs
    { duration: '30s', target: 50 },   // Ramp to 50 VUs
    { duration: '1m', target: 50 },    // Stay at 50 VUs
    { duration: '30s', target: 0 },    // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],  // 95% of requests < 500ms
    http_req_failed: ['rate<0.01'],    // <1% error rate
  },
};

const BASE_URL = __ENV.TARGET_URL || 'http://localhost:8000';

export default function () {
  // Health check
  const healthRes = http.get(`${BASE_URL}/health`);
  check(healthRes, {
    'health status is 200': (r) => r.status === 200,
    'health response time < 100ms': (r) => r.timings.duration < 100,
  });

  // Status endpoint (with fake job_id)
  const statusRes = http.get(`${BASE_URL}/status/00000000-0000-0000-0000-000000000000`);
  check(statusRes, {
    'status returns 404 for unknown job': (r) => r.status === 404,
  });

  // v1 endpoints
  const jobsRes = http.get(`${BASE_URL}/v1/jobs`);
  check(jobsRes, {
    'v1/jobs returns 200': (r) => r.status === 200,
  });

  // Metrics
  const metricsRes = http.get(`${BASE_URL}/metrics`);
  check(metricsRes, {
    'metrics returns 200': (r) => r.status === 200,
  });

  // Review pending
  const pendingRes = http.get(`${BASE_URL}/api/review/pending`);
  check(pendingRes, {
    'review/pending returns 200': (r) => r.status === 200,
  });

  sleep(1);
}
