import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:siggi_mobile/main.dart';

void main() {
  testWidgets('App boots to login or shell without crashing', (WidgetTester tester) async {
    await tester.pumpWidget(const SiggiApp());
    await tester.pump();
    expect(find.byType(MaterialApp), findsOneWidget);
  });
}
