import java.util.Base64
import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

// Fase 22 — versão central: o arquivo VERSION na raiz do repositório é a ÚNICA
// fonte. O Gradle o lê diretamente (sem syncing): mesmo VERSION -> backend,
// Desktop e Android. versionCode é derivado (monotônico por versão).
val vegaVersionFile = file("../../VERSION")
val vegaVersion: String = if (vegaVersionFile.exists()) {
    vegaVersionFile.readText().trim().removePrefix("v")
} else {
    "0.22.0"
}
val vegaVersionCode: Int = run {
    val parts = vegaVersion.split(".").mapNotNull { it.toIntOrNull() }
    if (parts.size >= 3) parts[0] * 10000 + parts[1] * 100 + parts[2] else 1
}

// Signing Fase 22 — o keystore NUNCA entra no Git. Fonte: android/keystore.properties
// (gitignored; ver android/keystore.properties.example). Alternativa para CI sem
// arquivo: variáveis de ambiente VEGA_KEYSTORE_BASE64/VEGA_KEYSTORE_PASSWORD/
// VEGA_KEYSTORE_KEY_ALIAS/VEGA_KEYSTORE_KEY_PASSWORD (GitHub Secrets).
val keystorePropsFile = rootProject.file("keystore.properties")
val keystoreProps = Properties().apply {
    if (keystorePropsFile.exists()) {
        keystorePropsFile.inputStream().use { load(it) }
    }
}
val envKeystoreBase64: String? = System.getenv("VEGA_KEYSTORE_BASE64")
val hasKeystore = keystorePropsFile.exists() || (!envKeystoreBase64.isNullOrBlank())
val keystorePath = if (keystorePropsFile.exists()) {
    keystoreProps.getProperty("storeFile", "keystore/vega-release.jks")
} else if (!envKeystoreBase64.isNullOrBlank()) {
    "keystore/ci-release.jks"
} else {
    ""
}

android {
    namespace = "app.vega.client"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.vega.client"
        minSdk = 26
        targetSdk = 34
        versionCode = vegaVersionCode
        versionName = vegaVersion

        // Conservador para obtenção rápida do primeiro build (artifacts locais).
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables { useSupportLibrary = true }
    }

    signingConfigs {
        if (hasKeystore) {
            create("release") {
                val storePass = if (keystorePropsFile.exists()) {
                    keystoreProps.getProperty("storePassword")
                } else {
                    System.getenv("VEGA_KEYSTORE_PASSWORD")
                }
                val keyAlias = if (keystorePropsFile.exists()) {
                    keystoreProps.getProperty("keyAlias")
                } else {
                    System.getenv("VEGA_KEYSTORE_KEY_ALIAS")
                }
                val keyPass = if (keystorePropsFile.exists()) {
                    keystoreProps.getProperty("keyPassword")
                } else {
                    System.getenv("VEGA_KEYSTORE_KEY_PASSWORD")
                }
                if (!envKeystoreBase64.isNullOrBlank() && !keystorePropsFile.exists()) {
                    val target = file(keystorePath)
                    target.parentFile?.mkdirs()
                    target.writeBytes(Base64.getDecoder().decode(envKeystoreBase64))
                }
                storeFile = file(keystorePath)
                storePassword = storePass
                this.keyAlias = keyAlias
                keyPassword = keyPass
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            if (hasKeystore) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.14"
    }
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.viewmodel.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.okhttp)
    debugImplementation(libs.androidx.ui.tooling)
    testImplementation(libs.junit)
    testImplementation(libs.org.json)
}