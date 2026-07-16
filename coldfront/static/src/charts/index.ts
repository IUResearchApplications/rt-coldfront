// SPDX-FileCopyrightText: (C) ColdFront Authors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { initPubChart } from './publicationChart';
import { initGrantChart } from './grantChart';
import { initAllocationChart } from './allocationChart';
import { initResourceChart } from './resourceChart';
import { initGaugeChart } from './gaugeChart';
import { initProjectTypeChart } from './projectTypeChart';
import { initProjectUserChart } from './projectUserChart';
import { initUsersChart } from './usersChart';
import { initUsersActiveChart } from './usersActiveChart';
import { initProjectReviewChart } from './projectReviewChart';

export function initCharts(): void {
  for (const func of [
    initPubChart,
    initGrantChart,
    initAllocationChart,
    initResourceChart,
    initGaugeChart,
    initProjectTypeChart,
    initProjectUserChart,
    initUsersChart,
    initUsersActiveChart,
    initProjectReviewChart,
  ]) {
    func();
  }
}
