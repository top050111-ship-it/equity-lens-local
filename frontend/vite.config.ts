import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath, URL} from 'node:url';
export default defineConfig(({mode})=>({
 plugins:[react()],
 define:mode==='public'?{'import.meta.env.VITE_PUBLIC_DEMO_ONLY':JSON.stringify('true')}:{},
 resolve:{alias:{'@':fileURLToPath(new URL('./src',import.meta.url))}},
 server:{port:5173,strictPort:true,proxy:{'/api':{target:'http://127.0.0.1:8000',changeOrigin:false}}},
 build:{sourcemap:false},
}));
