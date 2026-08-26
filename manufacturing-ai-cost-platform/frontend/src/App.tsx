import { AppRoutes } from './routes/AppRoutes';
import { Layout } from './components/Layout';
import { TooltipProvider } from './components/ui/tooltip';

export function App() {
  return (
    <TooltipProvider>
      <Layout>
        <AppRoutes />
      </Layout>
    </TooltipProvider>
  );
}
