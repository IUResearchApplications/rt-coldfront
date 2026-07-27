// SPDX-FileCopyrightText: (C) ColdFront Authors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { renderChart, ColorPalette } from './data';
import type { ChartData, ChartDataItem } from './data';
import { createDoughnutChart } from './doughnutChartFactory';
import type { Chart } from 'chart.js/auto';

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
  const chart = createDoughnutChart(canvas, {
    title: 'Project Statuses',
    labels: chartData.data.map((row: ChartDataItem) => row.name),
    values: chartData.data.map((row: ChartDataItem) => row.total),
    backgroundColors: ColorPalette.PRIMARY,
  });

  (canvas as ChartCanvas).projectReviewChart = chart;
}
