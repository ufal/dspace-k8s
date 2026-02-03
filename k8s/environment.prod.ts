export const environment = {
  production: true,
  // Use the runtime browser host/port so client requests do not hardcode container ports
  rest: { ssl: true, host: window.location.hostname, port: window.location.port || (window.location.protocol === 'https:' ? 443 : 80), nameSpace: '/server' },
  ui: { ssl: true, host: window.location.hostname, port: window.location.port || (window.location.protocol === 'https:' ? 443 : 80), nameSpace: '/' }
};
