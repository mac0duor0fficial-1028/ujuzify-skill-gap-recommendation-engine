import { jsPDF } from "jspdf";

declare global {
  interface Window {
    __UjuzifyJsPDF?: typeof jsPDF;
  }
}

window.__UjuzifyJsPDF = jsPDF;
