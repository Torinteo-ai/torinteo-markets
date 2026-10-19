import { createRoot } from 'react-dom/client';
import '@fontsource/roboto/400.css';
import '@fontsource/roboto/500.css';
import '@fontsource/roboto/600.css';
import '@fontsource/roboto/700.css';
import { App } from './App';
import './styles.css';

createRoot(document.getElementById('root')!).render(<App />);
