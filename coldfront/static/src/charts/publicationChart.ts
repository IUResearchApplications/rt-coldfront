// SPDX-FileCopyrightText: (C) ColdFront Authors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import Chart from 'chart.js/auto';
import { renderChart } from './data';
import type { ChartData, ChartDataItem } from './data';

export function initPubChart(): void {
  renderChart('pubs-by-year-chart', createPubChart);
}

function createPubChart(canvas: HTMLCanvasElement, chartData: ChartData): void {
  const pubTotal = document.getElementById('pubs-total');
  if (pubTotal) {
    pubTotal.textContent = chartData.total.toString();
  }

  new Chart(canvas, {
    type: 'bar',
    data: {
      labels: chartData.data.map((row: ChartDataItem) => row.name),
      datasets: [
        {
          label: 'Publications',
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
            text: 'Publications',
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
          title: {
            display: true,
            text: 'Year',
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
