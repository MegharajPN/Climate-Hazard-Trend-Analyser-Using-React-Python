import {
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
} from "chart.js";
import { Bar, Line } from "react-chartjs-2";

ChartJS.register(BarElement, CategoryScale, LinearScale, LineElement, PointElement, Tooltip, Legend);

const COLORS = {
  bars: "rgba(194, 65, 12, 0.65)",
  trend: "#7c2d12",
  line: "#0f766e",
  grid: "rgba(120, 120, 120, 0.2)",
};

const baseOptions = {
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: "index", intersect: false },
  plugins: { legend: { position: "bottom" } },
  scales: { x: { grid: { color: COLORS.grid } }, y: { grid: { color: COLORS.grid }, beginAtZero: true } },
};

export function EventCountChart({ plot, noun = "Events" }) {
  const data = {
    labels: plot.years,
    datasets: [
      {
        type: "bar",
        label: `${noun} per year`,
        data: plot.event_count,
        backgroundColor: COLORS.bars,
        order: 2,
      },
      {
        type: "line",
        label: "5-year rolling mean",
        data: plot.rolling_mean_5yr,
        borderColor: COLORS.trend,
        backgroundColor: COLORS.trend,
        pointRadius: 2,
        tension: 0.3,
        order: 1,
      },
    ],
  };
  return (
    <div className="chart-box">
      <Bar data={data} options={baseOptions} />
    </div>
  );
}

export function IntensityChart({ plot, units }) {
  const datasets = [
    {
      label: "Event days per year",
      data: plot.event_days,
      borderColor: COLORS.line,
      backgroundColor: COLORS.line,
      tension: 0.25,
      yAxisID: "y",
    },
  ];
  if (units?.intensity) {
    datasets.push({
      label: units.intensity,
      data: plot.mean_intensity,
      borderColor: COLORS.trend,
      backgroundColor: COLORS.trend,
      borderDash: [6, 4],
      tension: 0.25,
      yAxisID: "y1",
      spanGaps: true,
    });
  }
  const data = { labels: plot.years, datasets };
  const options = {
    ...baseOptions,
    scales: {
      x: { grid: { color: COLORS.grid } },
      y: { beginAtZero: true, position: "left", grid: { color: COLORS.grid } },
      y1: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false } },
    },
  };
  return (
    <div className="chart-box">
      <Line data={data} options={options} />
    </div>
  );
}
