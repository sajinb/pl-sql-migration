import { BrowserRouter, Routes, Route } from "react-router-dom";
import Header from "./components/Header";
import ProjectListPage from "./pages/ProjectListPage";
import ProjectDetailPage from "./pages/ProjectDetailPage";

export default function App() {
  return (
    <BrowserRouter>
      <Header />
      <Routes>
        <Route path="/" element={<ProjectListPage />} />
        <Route path="/projects/:id" element={<ProjectDetailPage />} />
      </Routes>
    </BrowserRouter>
  );
}
