import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { PatientApp } from "./patient/PatientApp";
import { StaffApp } from "./staff/StaffApp";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/intake" element={<PatientApp />} />
        <Route path="/staff/*" element={<StaffApp />} />
        <Route path="*" element={<Navigate to="/staff" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
