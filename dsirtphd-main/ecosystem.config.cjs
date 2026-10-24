// pm2 config — ONE process only. Never use cluster mode / "-i max" for this app:
// every exam lives in this single process (saved to data/runtime_state.json).
module.exports = {
  apps: [{
    name: 'phd-exam',
    script: 'dist/server.cjs',
    cwd: __dirname,
    instances: 1,
    exec_mode: 'fork',
    autorestart: true,
    max_memory_restart: '1500M',
    kill_timeout: 5000,          // gives the server time to save state on restart
    env: { NODE_ENV: 'production' },
  }],
};
