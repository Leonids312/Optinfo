import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'api.dart';
import 'screens.dart';
import 'reading_preferences.dart';
import 'updates.dart';

const ink = Color(0xFF182F31);
const teal = Color(0xFF257568);
const paper = Color(0xFFF7F8F4);

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  LicenseRegistry.addLicense(() async* {
    yield LicenseEntryWithLineBreaks(
      ['Noto Sans SC'],
      await rootBundle.loadString('OFL-NotoSansSC.txt'),
    );
  });
  final connection = ConnectionStore();
  String? loadError;
  try {
    await connection.load();
  } catch (_) {
    loadError = '无法读取系统凭据，请在连接设置中重新保存访问令牌。';
  }
  await ReadingPreferencesStore.shared.load();
  runApp(OptinfoApp(
      connection: connection,
      initialError: loadError,
      updates: UpdateService()));
}

class OptinfoApp extends StatelessWidget {
  final ConnectionStore connection;
  final String? initialError;
  final UpdateService? updates;
  final ReadingPreferencesStore? preferences;
  const OptinfoApp(
      {super.key,
      required this.connection,
      this.initialError,
      this.updates,
      this.preferences});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Optinfo',
        debugShowCheckedModeBanner: false,
        builder: (context, child) => ReadingPreferencesView(
          preferences: preferences ?? ReadingPreferencesStore.shared,
          child: child!,
        ),
        theme: ThemeData(
          useMaterial3: true,
          brightness: Brightness.light,
          colorScheme: ColorScheme.fromSeed(
            seedColor: teal,
            surface: paper,
          ).copyWith(primary: teal, onSurface: ink),
          scaffoldBackgroundColor: paper,
          fontFamily: 'NotoSansSC',
          fontFamilyFallback: const [
            'Microsoft YaHei',
            'Noto Sans CJK SC',
            'sans-serif',
          ],
          textTheme: const TextTheme(
            headlineLarge: TextStyle(
              fontSize: 34,
              height: 1.25,
              fontWeight: FontWeight.w700,
              letterSpacing: -1,
            ),
            headlineMedium: TextStyle(
              fontSize: 28,
              height: 1.35,
              fontWeight: FontWeight.w700,
              letterSpacing: -.5,
            ),
            titleLarge: TextStyle(
              fontSize: 21,
              height: 1.5,
              fontWeight: FontWeight.w600,
            ),
            titleMedium: TextStyle(
              fontSize: 16,
              height: 1.55,
              fontWeight: FontWeight.w600,
            ),
            bodyLarge: TextStyle(fontSize: 16, height: 1.8),
            bodyMedium: TextStyle(fontSize: 14, height: 1.65),
            labelSmall: TextStyle(fontSize: 11, letterSpacing: .5),
          ),
          appBarTheme: const AppBarTheme(
            backgroundColor: paper,
            surfaceTintColor: Colors.transparent,
            elevation: 0,
            centerTitle: false,
          ),
          dividerTheme: const DividerThemeData(
            color: Color(0xFFE0E6E0),
            thickness: 1,
          ),
          inputDecorationTheme: InputDecorationTheme(
            filled: true,
            fillColor: Colors.white,
            contentPadding: const EdgeInsets.symmetric(
              horizontal: 16,
              vertical: 15,
            ),
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(14),
              borderSide: const BorderSide(color: Color(0xFFDCE4DD)),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(14),
              borderSide: const BorderSide(color: Color(0xFFDCE4DD)),
            ),
          ),
          navigationBarTheme: const NavigationBarThemeData(
            backgroundColor: Colors.white,
            indicatorColor: Color(0xFFDCEEE7),
          ),
        ),
        home: HomeScreen(
            api: Api(connection), initialError: initialError, updates: updates),
      );
}
