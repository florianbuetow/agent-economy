import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, Navigate } from "react-router-dom";
import App from "./App";
import ObservatoryPage from "./pages/ObservatoryPage";
import TaskDrilldown from "./pages/TaskDrilldown";
import AgentProfile from "./pages/AgentProfile";
import QuarterlyReport from "./pages/QuarterlyReport";
import "./index.css";

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Navigate to="/observatory" replace /> },
      { path: "observatory", element: <ObservatoryPage /> },
      { path: "observatory/tasks/:taskId", element: <TaskDrilldown /> },
      { path: "observatory/agents/:agentId", element: <AgentProfile /> },
      { path: "observatory/quarterly", element: <QuarterlyReport /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>
);
