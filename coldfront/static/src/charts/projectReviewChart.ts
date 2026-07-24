// SPDX-FileCopyrightText: (C) ColdFront Authors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import Chart from 'chart.js/auto';
import { renderChart, ColorPalette } from './data';
import type { ChartData, ChartDataItem } from './data';

interface ChartCanvas extends HTMLCanvasElement {
  projectReviewChart?: Chart;
}

export function initProjectReviewChart(): void {
  renderChart('project-review-stats-chart', createProjectReviewChart);
}

function createProjectReviewChart(
  canvas: HTMLCanvasElement,
  chartData: ChartData
): void {
  const chart = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: chartData.data.map((row: ChartDataItem) => row.name),
      datasets: [
        {
          data: chartData.data.map((row: ChartDataItem) => row.total),
          backgroundColor: ColorPalette.PRIMARY,
          borderColor: ColorPalette.PRIMARY.map((color) => color + '80'),
          borderWidth: 1,
        },
      ],
    },
    options: {
      radius: '80%',
      cutout: '65%',
      responsive: true,
      plugins: {
        legend: {
          position: 'bottom',
        },
        title: {
          display: true,
          text: 'Project Statuses',
        },
      },
    },
  });

  (canvas as ChartCanvas).projectReviewChart = chart;
}
