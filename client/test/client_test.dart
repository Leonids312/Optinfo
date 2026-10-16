import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:optinfo/api.dart';
import 'package:optinfo/main.dart';
import 'package:optinfo/models.dart';

void main() {
  test(
    'connection rejects credentials, query strings and unencrypted remote URLs',
    () {
      expect(
        ConnectionStore.normalize(' https://example.ts.net:8443/ '),
        'https://example.ts.net:8443',
      );
      for (final address in [
        'http://example.com',
        'https://user:password@example.com',
        'https://example.com?token=secret',
        'https://example.com/#secret',
        'not-a-url',
      ]) {
        expect(
          () => ConnectionStore.normalize(address),
          throwsA(isA<ApiFailure>()),
        );
      }
    },
  );

  test('partial server records remain readable without invented values', () {
    final item = NewsItem({
      'id': 'doi:example',
      'title': '实验结果',
      'figures': null,
      'bookmarked': true,
    });
    expect(item.title, '实验结果');
    expect(item.summary, isEmpty);
    expect(item.figures, isEmpty);
    expect(item.bookmarked, isTrue);
    expect(Company({'id': 'x', 'name': '测试企业'}).listing, '上市信息待核实');
    expect(Company({'listed': false}).listing, '未上市');
    expect(
      Company({'listed': true, 'listing': 'NASDAQ: TEST'}).listing,
      'NASDAQ: TEST',
    );
  });

  for (final width in [360.0, 800.0, 1280.0]) {
    testWidgets('first connection and navigation fit ${width.toInt()} px', (
      tester,
    ) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(OptinfoApp(connection: ConnectionStore()));
      await tester.pumpAndSettle();
      expect(find.text('连接你的私人资讯库'), findsOneWidget);
      expect(tester.takeException(), isNull);
      if (width < 900) {
        expect(find.byType(NavigationBar), findsOneWidget);
        await tester.tap(find.text('企业'));
      } else {
        expect(find.byType(NavigationBar), findsNothing);
        await tester.tap(find.text('企业图鉴'));
      }
      await tester.pumpAndSettle();
      expect(find.text('认识创新背后的企业'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }
}
