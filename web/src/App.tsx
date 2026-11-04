import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import CostDashboard from "./pages/CostDashboard";
import MemoryDashboard from "./pages/MemoryDashboard";
import ReviewQueue from "./pages/ReviewQueue";
import RunConsole from "./pages/RunConsole";
import TraceExplorer from "./pages/TraceExplorer";

const client = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 1000 } },
});

export default function App() {
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Navigate to="/run" replace />} />
            <Route path="run/:taskId?" element={<RunConsole />} />
            <Route path="queue/:approvalId?" element={<ReviewQueue />} />
            <Route path="traces/:taskId?" element={<TraceExplorer />} />
            <Route path="memory" element={<MemoryDashboard />} />
            <Route path="cost" element={<CostDashboard />} />
            <Route path="*" element={<Navigate to="/run" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
