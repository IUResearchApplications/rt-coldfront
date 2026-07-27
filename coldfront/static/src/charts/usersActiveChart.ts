// SPDX-FileCopyrightText: (C) ColdFront Authors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import Chart from 'chart.js/auto';
import { renderChart } from './data';
import type { ChartData, ChartDataItem } from './data';

export function initUsersActiveChart(): void {
  renderChart('users-by-active-chart', createUsersActiveChart);
}

function createUsersActiveChart(
  canvas: HTMLCanvasElement,
  chartData: ChartData
): void {
  new Chart(canvas, {
    type: 'bar',
    data: {
      labels: chartData.data.map((row: ChartDataItem) => row.name),
      datasets: [
        {
          label: 'Users',
          data: chartData.data.map((row: ChartDataItem) => row.total),
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          suggestedMax: 10,
          ticks: {
            stepSize: 1,
            color: '#333',
          },
          grid: {
            display: false,
          },
          title: {
            display: true,
            text: 'Users',
            color: '#333',
          },
        },
        x: {
          grid: {
            display: false,
          },
          ticks: {
            color: '#333',
          },
        },
      },
      plugins: {
        title: {
          display: false,
        },
        legend: {
          display: false,
        },
      },
    },
  });
}
