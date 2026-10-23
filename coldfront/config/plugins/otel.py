# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from coldfront.config.base import INSTALLED_APPS, MIDDLEWARE

INSTALLED_APPS += ["coldfront.plugins.otel"]
MIDDLEWARE.append("coldfront.plugins.otel.middleware.ViewSpanMiddleware")
